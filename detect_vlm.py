"""Stage 1: find equipment by CATEGORY with a local vision model on Ollama, frame by frame, resumable.

Usage:
  .venv/bin/python detect_vlm.py --batch ollama-qwen35-all            # all query images, resumes where it stopped
  .venv/bin/python detect_vlm.py --batch test --start 1 --first 2      # a few frames
  nohup caffeinate -i .venv/bin/python detect_vlm.py --batch ollama-qwen35-all > logs/ollama-qwen35-all.log 2>&1 &

Each frame is cut into overlapping tiles over the horizon band (vlm_tools.tiles). The model returns one of the 19
English category names and a box on a 0-1000 scale PER AXIS of the tile (Qwen 3.5 through Ollama; Groq's Qwen 3.8
uses the longer side instead, see try_groq.py). Boxes are converted to panorama pixels, duplicates from overlapping
tiles are merged, invented runs of identical boxes and boxes lying on the vehicle are dropped.

Every frame's raw replies are saved in analysis/raw/<batch>/<image stem>.json; frames already saved are skipped, so the
run can be interrupted and restarted. When all requested frames are done, analysis/<batch>.json is written and
localize.py is run on it.

Stage 1 only knows categories. Categories with specific catalogue models (bus shelters, benches, barriers, litter bins,
lighting points, bollards, bicycle racks) get their fallback code and speed bumps the placeholder RAL01, all with
code_status "category_only"; direct categories (trees, hydrants...) get their final code. Stage 2 (typing_sheets.py,
apply_typing.py) assigns the specific codes.
"""
import argparse
import base64
import io
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

from PIL import Image

import detect
import fensch
from vlm_tools import BAND, iou, repetition_runs, tiles

DEFAULT_MODEL = "qwen3.5:9b"

CATEGORY_RULES = """Rules:
- One box per physical object; a row of bollards gives one box per bollard. Never invent repeated boxes.
- Bicycle racks are "Bicycle racks and shelters", not bollards. A pole carrying a lamp is a lighting point, not a bollard.
- Zebra stripes alone are not a speed bump; report a speed bump only with shark-teeth triangles, a ramp or a raised section.
- Ignore the car (roof, bonnet, mirrors, beacon, camera mount) and reflections on it.
- Public equipment only: ignore anything on private property (gardens, driveways, private car parks, behind fences or hedges), e.g. wheelie bins, garden trees, private planters, fences and gates.
- Trees: only street trees and trees in squares, public parks and public car parks; ignore woods, forest, hedgerows and trees in fields."""


def category_codes(catalogue):
    """English category name -> (stage-1 code, code_status)."""
    codes = {}
    for code, entry in catalogue.items():
        name = entry["category_en"]
        if entry["classification"] == "category_fallback":
            codes[name] = (code, "category_only")
        elif entry["classification"] == "direct_category":
            codes[name] = (code, "final")
    codes[catalogue["RAL01"]["category_en"]] = ("RAL01", "category_only")  # subtype decided in stage 2
    return codes


def prompt(names):
    return (f"Find street equipment in this crop of a street panorama taken from a camera on a car roof.\n"
            f"Categories (use exactly these names): {'; '.join(names)}.\n{CATEGORY_RULES}\n"
            'Output JSON: {"objects": [{"category": str, "bbox_2d": [x1, y1, x2, y2]}]} with coordinates on a 0-1000 scale. '
            "Empty list if nothing.")


def schema(names):
    return {"type": "object", "properties": {"objects": {"type": "array", "items": {"type": "object", "properties": {
        "category": {"type": "string", "enum": names},
        "bbox_2d": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}},
        "required": ["category", "bbox_2d"]}}}, "required": ["objects"]}


def ask_ollama(host, model, text, image_bytes, response_schema, retries=2):
    body = {"model": model, "stream": False, "think": False, "format": response_schema, "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 8192},
            "messages": [{"role": "user", "content": text, "images": [base64.b64encode(image_bytes).decode("ascii")]}]}
    request = urllib.request.Request(f"{host}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                return json.load(response)
        except Exception:
            if attempt == retries:
                raise
            time.sleep(10 * (attempt + 1))


def on_vehicle(box, width, height):
    """The car body fills the bottom of the panorama: drop boxes lying on it (reflections, roof equipment)."""
    cx, top = (box[0] + box[2]) / 2 / width, box[1] / height
    # Whole box below the roof edge in the middle of the image, or anywhere in the bottom fifth (bonnet, bars).
    return top >= 0.80 or (top >= 0.62 and 0.44 <= cx <= 0.76)


def detect_frame(args, image_path, names, response_schema):
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    calls, found = [], []
    text = prompt(names)
    for x0, y0, x1, y1 in tiles(width, height, args.tiles):
        buffer = io.BytesIO()
        image.crop((x0, y0, x1, y1)).save(buffer, "JPEG", quality=92)
        started = time.monotonic()
        reply = ask_ollama(args.host, args.model, text, buffer.getvalue(), response_schema)
        raw = reply["message"]["content"]
        calls.append({"tile": [x0, y0, x1, y1], "seconds": round(time.monotonic() - started, 2),
                      "prompt_tokens": reply.get("prompt_eval_count"), "completion_tokens": reply.get("eval_count"), "raw_text": raw})
        try:
            objects = json.loads(raw).get("objects", [])
        except json.JSONDecodeError:
            calls[-1]["error"] = "invalid JSON"
            continue
        tile_w, tile_h = x1 - x0, y1 - y0
        for obj in objects:
            box = obj.get("bbox_2d")
            if obj.get("category") not in names or not isinstance(box, list) or len(box) != 4:
                continue
            bx0, by0, bx1, by1 = min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])
            pixel = [x0 + bx0 / 1000 * tile_w, y0 + by0 / 1000 * tile_h, x0 + bx1 / 1000 * tile_w, y0 + by1 / 1000 * tile_h]
            pixel = [max(0, min(width, pixel[0])), max(0, min(height, pixel[1])), max(0, min(width, pixel[2])), max(0, min(height, pixel[3]))]
            if pixel[2] - pixel[0] >= 2 and pixel[3] - pixel[1] >= 2:
                found.append({"category": obj["category"], "code": obj["category"], "box": pixel})
    found.sort(key=lambda o: -(o["box"][2] - o["box"][0]) * (o["box"][3] - o["box"][1]))
    kept = []
    for obj in found:  # the same object seen in two overlapping tiles
        if not any(obj["category"] == other["category"] and iou(obj["box"], other["box"]) > 0.4 for other in kept):
            kept.append(obj)
    flagged = repetition_runs(kept)
    vehicle = [obj for i, obj in enumerate(kept) if i not in flagged and on_vehicle(obj["box"], width, height)]
    final = [obj for i, obj in enumerate(kept) if i not in flagged and obj not in vehicle]
    return {
        "source": "ollama", "model": args.model, "tiles": args.tiles, "band_fraction": list(BAND),
        "coordinates": "0-1000 per axis of each tile, converted to panorama pixels",
        "width": width, "height": height, "seconds": round(sum(c["seconds"] for c in calls), 2),
        "filtered_repetitions": [{"category": kept[i]["category"], "box": [round(v) for v in kept[i]["box"]]} for i in sorted(flagged)],
        "filtered_vehicle": [{"category": o["category"], "box": [round(v) for v in o["box"]]} for o in vehicle],
        "calls": calls,
        "objects": [{"category": o["category"], "box": [round(v) for v in o["box"]]} for o in final],
    }


def assemble(args, images, raw_dir, catalogue):
    codes = category_codes(catalogue)
    result_images = []
    for image_path in images:
        record = json.loads((raw_dir / f"{image_path.stem}.json").read_text(encoding="utf-8"))
        output = {"objects": [{"code": codes[o["category"]][0], "x_min": o["box"][0], "y_min": o["box"][1], "x_max": o["box"][2],
                               "y_max": o["box"][3], "confidence": "medium", "occluded": False, "truncated": False,
                               "visible_evidence": []} for o in record["objects"]]}
        objects, warnings = detect.to_objects(output, record["width"], record["height"], catalogue)
        for obj, source in zip(objects, record["objects"]):
            obj["code_status"] = codes[source["category"]][1]
        for warning in warnings:
            print(f"  {image_path.name}: {warning}", file=sys.stderr)
        result_images.append({"image_file": image_path.name, "width": record["width"], "height": record["height"], "objects": objects})
    batch = {
        "schema_version": "1.0.0", "coordinate_system": "normalized_xywh", "batch": args.batch,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "detector": {"source": "ollama", "model": args.model, "tiles": args.tiles, "stage": "category",
                     "note": "codes with code_status category_only still need stage 2 typing"},
        "images": result_images,
    }
    output_file = fensch.ANALYSIS_DIR / f"{args.batch}.json"
    output_file.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output_file} ({len(result_images)} images, {sum(len(i['objects']) for i in result_images)} objects)")
    return output_file


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--first", type=int, default=None, help="number of frames (default: all from --start)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--tiles", type=int, default=4)
    parser.add_argument("--no-localize", action="store_true")
    args = parser.parse_args()

    catalogue = fensch.load_catalogue()
    with fensch.CATEGORIES_FILE.open(encoding="utf-8-sig") as handle:
        import csv
        names = [row["name_en"] for row in csv.DictReader(handle)]
    response_schema = schema(names)
    images = fensch.query_images()[args.start:None if args.first is None else args.start + args.first]
    raw_dir = fensch.ANALYSIS_DIR / "raw" / args.batch
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "run.json").write_text(json.dumps({"model": args.model, "tiles": args.tiles, "prompt": prompt(names)}, indent=1), encoding="utf-8")

    todo = [p for p in images if not (raw_dir / f"{p.stem}.json").exists()]
    print(f"{len(images)} frames requested, {len(images) - len(todo)} already done, {len(todo)} to process with {args.model}", flush=True)
    started = time.monotonic()
    for done, image_path in enumerate(todo, start=1):
        try:
            record = detect_frame(args, image_path, names, response_schema)
        except Exception as error:  # keep going; the frame stays missing and is retried on the next run
            print(f"FAILED {image_path.name}: {type(error).__name__}: {error}", flush=True)
            continue
        target = raw_dir / f"{image_path.stem}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(target)
        elapsed = time.monotonic() - started
        eta = elapsed / done * (len(todo) - done)
        print(f"[{done}/{len(todo)}] {image_path.name}: {len(record['objects'])} objects, {record['seconds']:.0f}s "
              f"(dropped {len(record['filtered_repetitions'])} repeated, {len(record['filtered_vehicle'])} on vehicle); "
              f"ETA {eta / 3600:.1f} h", flush=True)

    missing = [p for p in images if not (raw_dir / f"{p.stem}.json").exists()]
    if missing:
        print(f"{len(missing)} frames still missing; run the same command again to retry them.")
        sys.exit(1)
    output_file = assemble(args, images, raw_dir, catalogue)
    if not args.no_localize:
        subprocess.run([sys.executable, "localize.py", str(output_file)], cwd=fensch.ROOT, check=True)
        subprocess.run(["node", "build-detection-viewer.mjs"], cwd=fensch.ROOT, check=False)


if __name__ == "__main__":
    main()
