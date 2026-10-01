"""Try a Qwen vision model on Groq with the detection prompt, writing detect.py replay records.

Usage:
  .venv/bin/python try_groq.py --batch groq-qwen38-test --start 1 --first 1 [--model qwen/qwen3.8-27b]
  .venv/bin/python detect.py --batch groq-qwen38-test --start 1 --first 1 --replay analysis/raw/groq-qwen38-test-replay

Reads GROQ_API_KEY (or GROQ_KEY) from the environment or from .env, never prints it.

Findings on qwen/qwen3.8-27b (frame 003255, 2026-10-01):
- Groq gives every image the same ~780-token budget, so a whole panorama is heavily downscaled: the panorama is cut
  into overlapping tiles over the horizon band, each sent separately (--tiles, default 4).
- Boxes come back on a 0-1000 scale of the tile's LONGER side on both axes (as if padded to a square), not 0-1000
  per axis. They are converted to panorama pixels here, and duplicates from overlapping tiles are merged.
- Known failure: rows of small identical objects (bollards) produce long runs of invented, evenly spaced boxes.
  repetition_runs() drops runs of >= 5 same-code boxes with near-identical sizes and regular spacing (real rows
  shrink with distance); dropped boxes are kept in the record under "filtered_repetitions".
"""
import argparse
import base64
import json
import os
import re
import time
from pathlib import Path

from PIL import Image

import detect
import fensch
from vlm_tools import iou, parse_json, repetition_runs, tile_corners, tile_urls, tiles, BAND

BATCH_PRICE = (0.40, 2.00)  # USD per Mtok for qwen/qwen3.8-27b: Groq's $0.80/$4.00 with the documented 50% batch discount



def tile_system_prompt(catalogue_text):
    rules = detect.prompt_sections(["CATALOGUE CODE RULES", "DETECTION AND CLASSIFICATION METHOD",
                                    "SPEED BUMPS AND PEDESTRIAN CROSSINGS", "CONFIDENCE"])
    return f"""You are a visual detection and classification system for public-space equipment. The image is a crop of a street panorama taken from a camera on a car roof. Return every visible object of the requested categories with a tight box and one catalogue code. Never report the car, its equipment or reflections on its body. Each box must enclose a real, distinct object: never invent repeated boxes. If no requested object is visible, return an empty list.

{rules}

BOXES: integer coordinates x_min, y_min, x_max, y_max on a 0-1000 scale.

{catalogue_text}
"""


def api_key():
    for name in ("GROQ_API_KEY", "GROQ_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    env_file = fensch.ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip().removeprefix("export ").strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                if key.strip() in ("GROQ_API_KEY", "GROQ_KEY"):
                    return value.strip().strip('"').strip("'")
    raise SystemExit("Set GROQ_API_KEY in the environment or in .env")


def objects_from_calls(calls, width, height, catalogue, keep_repetitions=False):
    """Panorama-pixel objects from the raw tile replies: convert, merge tile overlaps, drop invented runs."""
    found, invalid = [], 0
    for call in calls:
        x0, y0, x1, y1 = call["tile"]
        try:
            reply = parse_json(call["raw_text"])
        except ValueError as error:
            call["error"] = str(error)
            continue
        scale = max(x1 - x0, y1 - y0) / 1000  # both axes on 0-1000 of the tile's longer side
        for obj in reply.get("objects", []):
            corners = tile_corners(obj)
            if obj.get("code") not in catalogue or corners is None:
                invalid += 1
                continue
            box = [max(0, min(width, x0 + corners[0] * scale)), max(0, min(height, y0 + corners[1] * scale)),
                   max(0, min(width, x0 + corners[2] * scale)), max(0, min(height, y0 + corners[3] * scale))]
            if box[2] - box[0] >= 1 and box[3] - box[1] >= 1:
                found.append({k: v for k, v in obj.items() if k not in ("x_min", "y_min", "x_max", "y_max", "bbox_2d")} | {"box": box})
    found.sort(key=lambda o: -(o["box"][2] - o["box"][0]) * (o["box"][3] - o["box"][1]))
    kept = []
    for obj in found:  # merge the same object seen in two overlapping tiles
        if not any(catalogue[other["code"]]["category_fr"] == catalogue[obj["code"]]["category_fr"]
                   and iou(obj["box"], other["box"]) > 0.4 for other in kept):
            kept.append(obj)
    flagged = set() if keep_repetitions else repetition_runs(kept)
    dropped = [kept[i] for i in sorted(flagged)]
    kept = [obj for i, obj in enumerate(kept) if i not in flagged]
    defaults = {"confidence": "low", "occluded": False, "truncated": False, "visible_evidence": []}
    objects = [{**defaults, **{k: v for k, v in obj.items() if k != "box"},
                "x_min": round(obj["box"][0]), "y_min": round(obj["box"][1]),
                "x_max": round(obj["box"][2]), "y_max": round(obj["box"][3])} for obj in kept]
    return objects, found, dropped, invalid


def request_body(model, system, schema, data_url):
    return {"model": model, "temperature": 0, "max_completion_tokens": 8000, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": [
                {"type": "text", "text": "Detect the requested equipment. Answer with JSON only, matching this schema: " + json.dumps(schema)},
                {"type": "image_url", "image_url": {"url": data_url}}]}]}


def run_batch_api(client, args, images, system, schema, replay_dir):
    """Submit every tile of every image as one Groq batch job, wait for it, and return {image stem: calls}."""
    import tempfile
    # Measured on the 10-frame synchronous run: 27.5k input and at most ~10k output tokens per frame (4 tiles).
    estimate = len(images) * (27_500 * BATCH_PRICE[0] + 10_000 * BATCH_PRICE[1]) / 1e6
    print(f"{len(images)} frames x {args.tiles} tiles; worst-case estimate ${estimate:.2f} at batch prices "
          f"(${BATCH_PRICE[0]}/${BATCH_PRICE[1]} per Mtok, assuming Groq's 50% batch discount applies)")
    if estimate > args.max_usd:
        raise SystemExit(f"Estimate exceeds --max-usd {args.max_usd}; nothing submitted.")
    tile_of = {}
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
        for image_path in images:
            with Image.open(image_path) as opened:
                image = opened.convert("RGB")
            for number, (tile, url) in enumerate(tile_urls(image, args.tiles)):
                custom_id = f"{image_path.stem}|{number}"
                tile_of[custom_id] = tile
                handle.write(json.dumps({"custom_id": custom_id, "method": "POST", "url": "/v1/chat/completions",
                                         "body": request_body(args.model, system, schema, url)}) + "\n")
        input_path = Path(handle.name)
    size_mb = input_path.stat().st_size / 1e6
    if size_mb > 190:
        input_path.unlink()
        raise SystemExit(f"Batch file is {size_mb:.0f} MB (limit 200 MB): submit fewer frames per run.")
    try:
        uploaded = client.files.create(file=input_path.open("rb"), purpose="batch")
    finally:
        input_path.unlink()
    job = client.batches.create(input_file_id=uploaded.id, endpoint="/v1/chat/completions", completion_window=args.window)
    (replay_dir / "batch_job.json").write_text(json.dumps({"batch_id": job.id, "tiles": tile_of}, indent=1), encoding="utf-8")
    print(f"Submitted batch {job.id} ({size_mb:.1f} MB, {len(tile_of)} requests); waiting up to {args.wait_minutes} min")
    return collect_batch(client, job.id, tile_of, args.wait_minutes)


def collect_batch(client, batch_id, tile_of, wait_minutes):
    started = time.monotonic()
    while True:
        job = client.batches.retrieve(batch_id)
        if job.status in ("completed", "failed", "expired", "cancelled"):
            break
        if time.monotonic() - started > wait_minutes * 60:
            raise SystemExit(f"Batch {batch_id} still {job.status}; collect it later with --collect {batch_id}")
        time.sleep(15)
    print(f"Batch {batch_id}: {job.status} after {time.monotonic() - started:.0f}s; request counts {job.request_counts}")
    if job.error_file_id:
        print("errors:", client.files.content(job.error_file_id).text()[:1000])
    calls = {}
    if job.output_file_id:
        for line in client.files.content(job.output_file_id).text().splitlines():
            entry = json.loads(line)
            stem, _ = entry["custom_id"].split("|")
            body = (entry.get("response") or {}).get("body") or {}
            usage = body.get("usage") or {}
            choice = (body.get("choices") or [{}])[0]
            calls.setdefault(stem, []).append({
                "tile": tile_of[entry["custom_id"]], "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "raw_text": (choice.get("message") or {}).get("content") or "", "batch_id": batch_id})
    for stem in calls:
        calls[stem].sort(key=lambda call: call["tile"][0])
    return calls


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--first", type=int, default=1)
    parser.add_argument("--model", default="qwen/qwen3.8-27b")
    parser.add_argument("--tiles", type=int, default=4)
    parser.add_argument("--keep-repetitions", action="store_true", help="disable the filter for invented runs of identical boxes")
    parser.add_argument("--reparse", action="store_true", help="rebuild objects from the saved replies of an earlier run, without API calls")
    parser.add_argument("--batch-api", action="store_true", help="send all tiles as one Groq batch job (50%% cheaper, asynchronous)")
    parser.add_argument("--collect", metavar="BATCH_ID", help="collect a batch job submitted earlier with --batch-api")
    parser.add_argument("--window", default="24h", help="batch completion window (24h to 7d)")
    parser.add_argument("--wait-minutes", type=float, default=60)
    parser.add_argument("--max-usd", type=float, default=1.0, help="refuse to submit a batch whose worst-case estimate is higher")
    args = parser.parse_args()

    if not args.reparse:
        from groq import Groq
        client = Groq(api_key=api_key())
    images = fensch.query_images()[args.start:args.start + args.first]
    catalogue = fensch.load_catalogue()
    schema = detect.response_schema(sorted(catalogue))
    system = tile_system_prompt("REFERENCE DATA\n" + fensch.CATEGORIES_FILE.read_text(encoding="utf-8-sig").strip()
                                + "\n\n" + fensch.DESCRIPTIONS_FILE.read_text(encoding="utf-8").strip())
    replay_dir = fensch.ANALYSIS_DIR / "raw" / f"{args.batch}-replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    batch_calls = None
    if args.collect:
        job_file = json.loads((replay_dir / "batch_job.json").read_text(encoding="utf-8"))
        batch_calls = collect_batch(client, args.collect, job_file["tiles"], args.wait_minutes)
    elif args.batch_api:
        batch_calls = run_batch_api(client, args, images, system, schema, replay_dir)

    for image_path in images:
        image = Image.open(image_path).convert("RGB")
        width, height = image.size
        calls, seconds = [], 0.0
        record_file = replay_dir / f"{image_path.stem}.json"
        if args.reparse:
            previous = json.loads(record_file.read_text(encoding="utf-8"))
            calls, seconds = previous["calls"], previous["seconds"]
        elif batch_calls is not None:
            calls = batch_calls.get(image_path.stem, [])
            if len(calls) < args.tiles:
                print(f"  {image_path.name}: only {len(calls)} of {args.tiles} tile replies in the batch output")
        for (x0, y0, x1, y1), url in ([] if args.reparse or batch_calls is not None else tile_urls(image, args.tiles)):
            started = time.monotonic()
            response = client.chat.completions.create(**request_body(args.model, system, schema, url))
            seconds += time.monotonic() - started
            text = response.choices[0].message.content or ""
            calls.append({"tile": [x0, y0, x1, y1], "prompt_tokens": response.usage.prompt_tokens,
                          "completion_tokens": response.usage.completion_tokens, "raw_text": text})
        objects, found, dropped, invalid = objects_from_calls(calls, width, height, catalogue, args.keep_repetitions)
        record = {"source": "groq-batch" if batch_calls is not None else "groq", "model": args.model, "tiles": args.tiles, "band_fraction": list(BAND),
                  "coordinates": "0-1000 of each tile's longer side, converted to panorama pixels",
                  "seconds": round(seconds, 2), "skipped_objects": invalid,
                  "filtered_repetitions": [{"code": o["code"], "box": [round(v) for v in o["box"]]} for o in dropped], "usage": None, "calls": calls, "output": {"objects": objects}}
        record_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tokens = sum(c["prompt_tokens"] for c in calls), sum(c["completion_tokens"] for c in calls)
        print(f"{image_path.name}: {len(objects)} objects ({len(found)} before merging tiles) in {seconds:.1f}s, "
              f"{tokens[0]} prompt + {tokens[1]} completion tokens, {invalid} malformed skipped, {len(dropped)} repeated boxes filtered")
    print(f"Replay records in {replay_dir}; build a batch with: .venv/bin/python detect.py --batch {args.batch} "
          f"--start {args.start} --first {args.first} --replay {replay_dir}")


if __name__ == "__main__":
    main()
