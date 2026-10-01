"""Stage 2, step 1: contact sheets for choosing the specific catalogue code of each merged feature.

Usage:
  .venv/bin/python typing_sheets.py analysis/<batch>.json [--per-sheet 12] [--views 2] [--all-specific]

Input: a localized batch (localize.py has added `features`). Features whose detections still carry code_status
"category_only" (stage 1 of detect_vlm.py) are selected; --all-specific also selects every feature of a category that
has specific models (useful for batches without code_status). For each feature, the best views are cropped from the
panoramas (closest, then not occluded/truncated), with a margin for context, and laid out on sheets per category, under
a strip with that category's reference photos (images/<code>.png) and codes.

Output in analysis/raw/<batch>/typing/:
  <category>_<n>.jpg   sheets to inspect, items numbered #1, #2... (numbering is global to the batch)
  index.json           item number -> feature_id, category, candidate codes, views
  decisions.json       template to fill: one entry per feature, "code": null until decided (see apply_typing.py)
An existing decisions.json is never overwritten.
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import fensch

SPECIFIC_PREFIXES = ["ABR", "BAN", "BAR", "COR", "ECL", "POT", "VEL", "RAL"]
SHEET_WIDTH = 1800


def font(size):
    for name in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def best_views(feature, objects_by_key, count):
    views = []
    for detection in feature["detections"]:
        image, obj = objects_by_key[(detection["image_file"], detection["object_id"])]
        ground = obj.get("ground", {})
        rank = (0 if ground.get("reliable") else 1, 1 if obj.get("truncated") else 0, detection["distance_m"])
        views.append((rank, image, obj))
    views.sort(key=lambda view: view[0])
    return [(image, obj) for _, image, obj in views[:count]]


def crop(image_file, obj, image_size, flat):
    width, height = image_size
    box = obj["bbox"]
    x0, y0 = box["x"] * width, box["y"] * height
    x1, y1 = x0 + box["width"] * width, y0 + box["height"] * height
    margin = 0.6 if flat else 0.35
    pad_x, pad_y = max(24, (x1 - x0) * margin), max(24, (y1 - y0) * margin)
    region = (max(0, x0 - pad_x), max(0, y0 - pad_y), min(width, x1 + pad_x), min(height, y1 + pad_y))
    with Image.open(fensch.QUERY_DIR / image_file) as source:
        piece = source.convert("RGB").crop(tuple(round(v) for v in region))
    # Mark the detected box inside its context crop.
    draw = ImageDraw.Draw(piece)
    draw.rectangle([x0 - region[0], y0 - region[1], x1 - region[0], y1 - region[1]], outline=(255, 40, 40), width=1)
    target_height = 260
    scale = min(target_height / piece.height, 380 / piece.width, 6.0)
    if scale > 1 or piece.height > target_height:
        piece = piece.resize((max(1, round(piece.width * scale)), max(1, round(piece.height * scale))), Image.LANCZOS)
    return piece, [round(v) for v in region]


def reference_strip(codes, label_font):
    pictures = []
    for code in codes:
        path = fensch.ROOT / "images" / f"{code}.png"
        if not path.exists():
            continue
        with Image.open(path) as source:
            picture = source.convert("RGB")
        picture.thumbnail((240, 170))
        pictures.append((code, picture))
    if not pictures:
        return None
    strip = Image.new("RGB", (SHEET_WIDTH, 210), (245, 245, 240))
    draw = ImageDraw.Draw(strip)
    x = 10
    for code, picture in pictures:
        if x + picture.width > SHEET_WIDTH:
            break
        strip.paste(picture, (x, 8))
        draw.text((x, 182), code, fill=(20, 20, 20), font=label_font)
        x += max(picture.width, 120) + 16
    return strip


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("analysis_file")
    parser.add_argument("--per-sheet", type=int, default=12)
    parser.add_argument("--views", type=int, default=2)
    parser.add_argument("--all-specific", action="store_true")
    args = parser.parse_args()

    path = Path(args.analysis_file)
    data = json.loads(path.read_text(encoding="utf-8"))
    if "features" not in data:
        raise SystemExit("Run localize.py on the batch first.")
    catalogue = fensch.load_catalogue()
    objects_by_key = {(image["image_file"], obj["object_id"]): (image, obj) for image in data["images"] for obj in image["objects"]}
    out_dir = fensch.ANALYSIS_DIR / "raw" / (data.get("batch") or path.stem) / "typing"
    out_dir.mkdir(parents=True, exist_ok=True)
    label_font, small_font = font(22), font(16)

    selected = {}
    for feature in data["features"]:
        prefix = fensch.code_prefix(feature["code"])
        if prefix not in SPECIFIC_PREFIXES:
            continue
        members = [objects_by_key[(d["image_file"], d["object_id"])][1] for d in feature["detections"]]
        if args.all_specific or any(m.get("code_status") == "category_only" for m in members):
            selected.setdefault(prefix, []).append(feature)

    index, number = [], 0
    for prefix in SPECIFIC_PREFIXES:
        features = sorted(selected.get(prefix, []), key=lambda f: f["min_distance_m"])
        if not features:
            continue
        codes = sorted(code for code in catalogue if fensch.code_prefix(code) == prefix)
        strip = reference_strip([c for c in codes if catalogue[c]["classification"] in ("described_model", "shape_subtype")], small_font)
        for sheet_number, start in enumerate(range(0, len(features), args.per_sheet), start=1):
            items = []
            for feature in features[start:start + args.per_sheet]:
                number += 1
                pieces, views = [], []
                for image, obj in best_views(feature, objects_by_key, args.views):
                    piece, region = crop(image["image_file"], obj, (image["width"], image["height"]), prefix == "RAL")
                    pieces.append(piece)
                    views.append({"image_file": image["image_file"], "object_id": obj["object_id"],
                                  "distance_m": obj.get("ground", {}).get("distance_m"), "crop": region})
                items.append((number, feature, pieces))
                index.append({"number": number, "sheet": None, "feature_id": feature["feature_id"], "category_en": feature["category_en"],
                              "current_code": feature["code"], "candidate_codes": codes, "n_detections": feature["n_detections"],
                              "min_distance_m": feature["min_distance_m"], "views": views})
            # Lay the items out in rows.
            cells = []
            for item_number, feature, pieces in items:
                cell_width = sum(p.width for p in pieces) + 6 * (len(pieces) - 1) + 12
                cell_height = max((p.height for p in pieces), default=40) + 40
                cell = Image.new("RGB", (cell_width, cell_height), (255, 255, 255))
                x = 6
                for piece in pieces:
                    cell.paste(piece, (x, 34))
                    x += piece.width + 6
                ImageDraw.Draw(cell).text((6, 6), f"#{item_number}  {feature['feature_id']}  {feature['min_distance_m']:.0f} m",
                                          fill=(180, 0, 0), font=label_font)
                cells.append(cell)
            rows, row, row_width = [], [], 0
            for cell in cells:
                if row and row_width + cell.width > SHEET_WIDTH:
                    rows.append(row)
                    row, row_width = [], 0
                row.append(cell)
                row_width += cell.width + 10
            if row:
                rows.append(row)
            top = strip.height + 10 if strip else 0
            sheet_height = top + sum(max(c.height for c in r) + 10 for r in rows) + 10
            sheet = Image.new("RGB", (SHEET_WIDTH, sheet_height), (225, 228, 224))
            if strip:
                sheet.paste(strip, (0, 0))
            y = top
            for r in rows:
                x = 10
                for cell in r:
                    sheet.paste(cell, (x, y))
                    x += cell.width + 10
                y += max(c.height for c in r) + 10
            name = f"{prefix}_{sheet_number:02d}.jpg"
            sheet.save(out_dir / name, quality=88)
            for entry in index[-len(items):]:
                entry["sheet"] = name
            print(f"{name}: items #{items[0][0]}-#{items[-1][0]}")

    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    decisions_file = out_dir / "decisions.json"
    if decisions_file.exists():
        print(f"Kept existing {decisions_file}")
    else:
        template = {entry["feature_id"]: {"number": entry["number"], "category_en": entry["category_en"], "code": None,
                                          "confidence": None, "evidence": []} for entry in index}
        decisions_file.write_text(json.dumps(template, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Wrote {decisions_file} ({len(template)} features to decide)")
    print(f"{len(index)} features on sheets in {out_dir}")


if __name__ == "__main__":
    main()
