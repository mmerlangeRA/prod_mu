"""Detect catalogue equipment in query panoramas with a Claude vision model.

Usage:
  .venv/bin/python detect.py --batch NAME [--first 10] [--model claude-sonnet-5-5] [--effort medium]
  .venv/bin/python detect.py --batch NAME --first 10 --replay DIR   # read DIR/<image stem>.json instead of calling the API
  .venv/bin/python detect.py --batch NAME --first 1 --dry-run      # write the request that would be sent, no API call

Writes analysis/NAME.json (the fensch_bbox_prompt.md output format, normalized xywh) and keeps every raw
model response in analysis/raw/NAME/. An existing batch is never overwritten.
"""
import argparse
import base64
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

import fensch

DEFAULT_MODEL = "claude-sonnet-5-5"
# USD per million tokens (input, output, cache write 5 min, cache read).
PRICES = {"claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20), "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20)}
# A box whose bottom is above this fraction of the image height is more than 15 m away for a 2 m camera height.
MAX_RANGE_ROW = 0.542


def prompt_sections(names):
    """Return the named upper-case sections of fensch_bbox_prompt.md, which stays the single source of the code rules."""
    text = fensch.PROMPT_FILE.read_text(encoding="utf-8").split("\n---\n")[1]
    sections, current = {}, None
    for line in text.splitlines():
        if re.fullmatch(r"[A-Z][A-Z ]+", line.strip()):
            current = line.strip()
            sections[current] = []
        elif current:
            sections[current].append(line)
    missing = [name for name in names if name not in sections]
    if missing:
        raise SystemExit(f"Sections not found in {fensch.PROMPT_FILE.name}: {missing}")
    return "\n\n".join(name + "\n" + "\n".join(sections[name]).strip() for name in names)


def system_prompt(width, height, coordinates="pixels"):
    """coordinates="pixels" asks for image pixels (Claude); "per_mille" asks for a 0-1000 scale on both axes (Qwen models)."""
    rules = prompt_sections(["CATALOGUE CODE RULES", "DETECTION AND CLASSIFICATION METHOD", "SPEED BUMPS AND PEDESTRIAN CROSSINGS", "CONFIDENCE"])
    if coordinates == "per_mille":
        horizon, limit = "y = 500", f"y = {round(1000 * MAX_RANGE_ROW)}"
        box_rule = ("Give integer coordinates on a 0-1000 scale relative to the image, on both axes: x_min, y_min (top-left), "
                    "x_max, y_max (bottom-right), with 0 <= x_min < x_max <= 1000 and 0 <= y_min < y_max <= 1000.")
    else:
        horizon, limit = f"row {height // 2}", f"row {round(height * MAX_RANGE_ROW)}"
        box_rule = (f"Give integer pixel coordinates in the image exactly as supplied: x_min, y_min (top-left), x_max, y_max "
                    f"(bottom-right), with 0 <= x_min < x_max <= {width} and 0 <= y_min < y_max <= {height}.")
    return f"""You are a visual detection and classification system for public-space equipment. Inspect the supplied street panorama and return every visible object belonging to one of the requested categories, with a tight bounding box and exactly one catalogue code.

PANORAMA GEOMETRY

- The image is a 360° × 180° equirectangular panorama of {width} × {height} px. The horizontal centre faces the front of the vehicle. The left and right edges show the same direction (behind the vehicle) and join seamlessly.
- The horizon is at about {horizon}. Straight lines that are not vertical appear curved.
- The camera is mounted on the roof of a car. The car body (roof, bonnet, windows, mirrors, rotating beacon, camera mount, cables) fills the lower part of the image and reflects the surroundings. Never report the car, its equipment or anything reflected on its body.
- Report only objects whose base (ground contact) is below {limit}. An object whose base is higher in the image is more than about 15 m away and will be captured from a closer frame.
- An object cut by the left/right edge: report only the part containing its base, clipped to the image.

{rules}

BOUNDING BOXES

- One tight axis-aligned box per physical object, around its visible extent: include attached parts (shelter roof, bench supports, bollard base, bicycle loop), exclude shadows, reflections and neighbouring objects.
- {box_rule}
- For a tree, the box covers trunk and canopy; its bottom edge is the trunk base.

EVIDENCE

- For a category fallback, visible_evidence states the category evidence and at least one structural reason it does not match the closest described models.
- For a described model, list two independent visible traits when available.
- Keep evidence short and observable. If no requested object is visible, return an empty objects list.

REFERENCE DATA

categories_to_collect_fr_en.csv:
{fensch.CATEGORIES_FILE.read_text(encoding="utf-8-sig").strip()}

fensch_image_descriptions.json:
{fensch.DESCRIPTIONS_FILE.read_text(encoding="utf-8").strip()}
"""


def response_schema(codes):
    item = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "enum": codes},
            "x_min": {"type": "integer"},
            "y_min": {"type": "integer"},
            "x_max": {"type": "integer"},
            "y_max": {"type": "integer"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "occluded": {"type": "boolean"},
            "truncated": {"type": "boolean"},
            "visible_evidence": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["code", "x_min", "y_min", "x_max", "y_max", "confidence", "occluded", "truncated", "visible_evidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"objects": {"type": "array", "items": item}},
        "required": ["objects"],
        "additionalProperties": False,
    }


def build_request(args, image_path, width, height, catalogue):
    image_data = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    return {
        "model": args.model,
        "max_tokens": 16000,
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
        "output_config": {"effort": args.effort, "format": {"type": "json_schema", "schema": response_schema(sorted(catalogue))}},
        "system": [{"type": "text", "text": system_prompt(width, height), "cache_control": {"type": "ephemeral"}}],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
                {"type": "text", "text": f"Detect the requested equipment in this {width} × {height} px panorama."},
            ],
        }],
    }


def call_api(client, request):
    response = client.beta.messages.create(**request)
    if response.stop_reason == "refusal":
        details = response.stop_details
        raise RuntimeError(f"refused ({getattr(details, 'category', None)}): {getattr(details, 'explanation', '')}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("response truncated at max_tokens")
    text = next(block.text for block in response.content if block.type == "text")
    usage = response.usage
    return {
        "source": "api",
        "model": response.model,
        "request_id": response._request_id,
        "stop_reason": response.stop_reason,
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens or 0,
            "cache_read_input_tokens": usage.cache_read_input_tokens or 0,
        },
        "output": json.loads(text),
    }


def cost_usd(model, usage):
    if model not in PRICES or not usage:
        return None
    price_in, price_out, price_write, price_read = PRICES[model]
    return (usage["input_tokens"] * price_in + usage["output_tokens"] * price_out
            + usage["cache_creation_input_tokens"] * price_write + usage["cache_read_input_tokens"] * price_read) / 1e6


def to_objects(output, width, height, catalogue):
    """Convert model pixel boxes to the fensch_bbox_prompt.md object format; drop invalid entries with a warning."""
    objects, warnings = [], []
    for index, raw in enumerate(output.get("objects", []), start=1):
        code = raw.get("code")
        if code not in catalogue:
            warnings.append(f"object {index}: unknown code {code!r}")
            continue
        x_min, x_max = sorted((max(0, min(width, raw["x_min"])), max(0, min(width, raw["x_max"]))))
        y_min, y_max = sorted((max(0, min(height, raw["y_min"])), max(0, min(height, raw["y_max"]))))
        if x_max - x_min < 1 or y_max - y_min < 1:
            warnings.append(f"object {index}: empty box after clipping")
            continue
        entry = catalogue[code]
        objects.append({
            "object_id": f"object_{len(objects) + 1}",
            "code": code,
            "category_fr": entry["category_fr"],
            "category_en": entry["category_en"],
            "classification": entry["classification"],
            # Round the edges, not the size, so that x + width never exceeds 1 after rounding.
            "bbox": {
                "x": round(x_min / width, 6),
                "y": round(y_min / height, 6),
                "width": round(round(x_max / width, 6) - round(x_min / width, 6), 6),
                "height": round(round(y_max / height, 6) - round(y_min / height, 6), 6),
            },
            "bbox_px": [x_min, y_min, x_max, y_max],
            "confidence": raw["confidence"],
            "occluded": raw["occluded"],
            "truncated": raw["truncated"],
            "visible_evidence": raw["visible_evidence"],
        })
    return objects, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", required=True, help="batch name; output goes to analysis/<batch>.json")
    parser.add_argument("--first", type=int, default=10, help="number of query images, in (video, frame) order")
    parser.add_argument("--start", type=int, default=0, help="index of the first query image")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--replay", help="directory of saved responses <image stem>.json to use instead of the API")
    parser.add_argument("--dry-run", action="store_true", help="write the first request to analysis/raw/<batch>/request.json and stop")
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.batch):
        raise SystemExit("Batch names may contain only letters, digits, '.', '_' and '-'.")
    output_file = fensch.ANALYSIS_DIR / f"{args.batch}.json"
    raw_dir = fensch.ANALYSIS_DIR / "raw" / args.batch
    if output_file.exists() and not args.dry_run:
        raise SystemExit(f"{output_file} already exists; choose another batch name.")

    catalogue = fensch.load_catalogue()
    images = fensch.query_images()[args.start:args.start + args.first]
    raw_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        with Image.open(images[0]) as image:
            width, height = image.size
        request = build_request(args, images[0], width, height, catalogue)
        request["messages"][0]["content"][0]["source"]["data"] = f"<{images[0].name}, base64 omitted>"
        (raw_dir / "request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {raw_dir / 'request.json'}; system prompt {len(request['system'][0]['text'])} characters.")
        return

    client = None
    if not args.replay:
        import anthropic
        client = anthropic.Anthropic()

    result_images, total_cost, failures = [], 0.0, 0
    for image_path in images:
        with Image.open(image_path) as image:
            width, height = image.size
        started = time.monotonic()
        try:
            if args.replay:
                record = json.loads((Path(args.replay) / f"{image_path.stem}.json").read_text(encoding="utf-8"))
            else:
                record = call_api(client, build_request(args, image_path, width, height, catalogue))
        except Exception as error:  # keep going; the failure is recorded in the batch
            failures += 1
            print(f"FAILED {image_path.name}: {error}", file=sys.stderr)
            result_images.append({"image_file": image_path.name, "width": width, "height": height, "objects": [], "error": str(error)})
            continue
        (raw_dir / f"{image_path.stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        objects, warnings = to_objects(record["output"], width, height, catalogue)
        cost = cost_usd(record.get("model"), record.get("usage"))
        total_cost += cost or 0
        for warning in warnings:
            print(f"  {image_path.name}: {warning}", file=sys.stderr)
        result_images.append({"image_file": image_path.name, "width": width, "height": height, "objects": objects})
        cost_text = f", ${cost:.4f}" if cost is not None else ""
        print(f"{image_path.name}: {len(objects)} objects ({time.monotonic() - started:.1f}s{cost_text})")

    batch = {
        "schema_version": "1.0.0",
        "coordinate_system": "normalized_xywh",
        "batch": args.batch,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "detector": {
            "source": "replay" if args.replay else "api",
            "replay_dir": args.replay,
            "model": args.model,
            "effort": args.effort,
            "prompt": fensch.PROMPT_FILE.name,
            "estimated_cost_usd": round(total_cost, 4) if not args.replay else None,
        },
        "images": result_images,
    }
    output_file.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output_file} ({len(result_images)} images, {failures} failed).")
    if not args.replay:
        print(f"Estimated cost: ${total_cost:.4f}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
