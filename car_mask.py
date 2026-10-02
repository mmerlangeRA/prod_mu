"""Step 1: the survey car's mask for one video (the camera is fixed on the car, so one mask serves every frame).

Usage:
  .venv/bin/python car_mask.py prepare [--video NAME] [--frames 60]   # reference images to trace the outline on
  .venv/bin/python car_mask.py render  [--video NAME] [--margin 4]    # outline.json -> mask.png + check images

Why not fully automatic: the car body is glossy and reflects the moving scene, so its pixels vary as much as the scene's,
and its outline has gaps wherever the paint matches the background. What is stable is the car's geometry, so:

1. `prepare` takes --frames frames spread over the video and writes, in car_masks/<video>/:
   - median.jpg: per-pixel median, where the car stays sharp and the scene blurs out;
   - stable_edges.png: per-pixel median of the gradient magnitude, where only edges present in every frame survive
     (roof edge, rails, mirror, beacon, camera mast, cables);
   - grid_left.jpg / grid_right.jpg: the lower part of median.jpg with a normalized (u, v) grid, for tracing.
2. The outline is traced once, by looking at those images, into car_masks/<video>/outline.json:
   {"top_line": [[u, v], ...]}: the car's upper outline from u = 0 to u = 1 (spikes for the mast, beacon, mirror...);
   everything below it is the car. Optional "extra_polygons": [[[u, v], ...], ...] for parts above the line.
3. `render` rasterizes it to mask.png (full panorama size, 255 = car, widened by --margin pixels for vibrations) and
   writes check.jpg (the mask over the stable edges and three frames), and reports the share of stable edges in the
   lower half that the mask misses: car edges left outside the outline.

Used by detect_vlm.py (car greyed out in the tiles, boxes mostly on the car dropped) and localize.py (a ground-contact
point on the car is not reliable: the base is hidden by the car).
"""
import argparse
import json

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import fensch


def frames_of(video):
    return [path for path in fensch.query_images() if fensch.frame_key(path.name)[0] == video]


def font(size):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def grid_image(image, u0, u1, v0, v1, scale):
    width, height = image.size
    crop = image.crop((round(u0 * width), round(v0 * height), round(u1 * width), round(v1 * height)))
    crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS)
    draw, label = ImageDraw.Draw(crop), font(14)
    for k in range(round(u0 * 100), round(u1 * 100) + 1, 2):
        x = (k / 100 - u0) * width * scale
        draw.line([(x, 0), (x, crop.height)], fill=(255, 255, 0) if k % 10 == 0 else (0, 255, 255), width=1)
        draw.text((x + 2, 2), f"{k / 100:.2f}", fill=(255, 255, 0), font=label)
    for k in range(round(v0 * 100), round(v1 * 100) + 1, 2):
        y = (k / 100 - v0) * height * scale
        draw.line([(0, y), (crop.width, y)], fill=(255, 255, 0) if k % 10 == 0 else (0, 255, 255), width=1)
        draw.text((2, y + 2), f"{k / 100:.2f}", fill=(255, 0, 255), font=label)
    return crop


def prepare(video, count):
    frames = frames_of(video)
    if not frames:
        raise SystemExit(f"No query image of video {video}.")
    picked = frames[::max(1, len(frames) // count)][:count]
    colour, gradients = [], []
    for path in picked:
        image = Image.open(path).convert("RGB")
        colour.append(np.asarray(image, dtype=np.uint8))
        grey = np.asarray(image.convert("L"), dtype=np.float32)
        gx, gy = np.zeros_like(grey), np.zeros_like(grey)
        gx[:, 1:-1], gy[1:-1, :] = grey[:, 2:] - grey[:, :-2], grey[2:, :] - grey[:-2, :]
        gradients.append(np.hypot(gx, gy))
    out = fensch.CAR_MASK_DIR / video
    out.mkdir(parents=True, exist_ok=True)
    median = Image.fromarray(np.median(np.stack(colour), axis=0).astype(np.uint8))
    stable = np.median(np.stack(gradients), axis=0)
    median.save(out / "median.jpg", quality=92)
    Image.fromarray(np.clip(stable * 4, 0, 255).astype(np.uint8)).save(out / "stable_edges.png")
    np.save(out / "stable_edges.npy", stable.astype(np.float16))
    grid_image(median, 0.0, 0.5, 0.44, 1.0, 1.8).save(out / "grid_left.jpg", quality=90)
    grid_image(median, 0.5, 1.0, 0.44, 1.0, 1.8).save(out / "grid_right.jpg", quality=90)
    print(f"{len(picked)} frames of {video}: wrote median.jpg, stable_edges.png, grid_left.jpg, grid_right.jpg in {out}")
    if not (out / "outline.json").exists():
        print(f"Next: trace the car outline into {out / 'outline.json'}, then run: car_mask.py render --video {video}")


def render(video, margin):
    out = fensch.CAR_MASK_DIR / video
    outline = json.loads((out / "outline.json").read_text(encoding="utf-8"))
    frames = frames_of(video)
    with Image.open(frames[0]) as first:
        width, height = first.size
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    line = [(u * width, v * height) for u, v in outline["top_line"]]
    draw.polygon(line + [(width, height), (0, height)], fill=255)
    for polygon in outline.get("extra_polygons", []):
        draw.polygon([(u * width, v * height) for u, v in polygon], fill=255)
    if margin:
        mask = mask.filter(ImageFilter.MaxFilter(2 * margin + 1))
    mask.save(out / "mask.png")

    car = np.asarray(mask) > 127
    report = {"video": video, "size": [width, height], "margin_px": margin, "masked_share": round(float(car.mean()), 4)}
    edges_file = out / "stable_edges.npy"
    if edges_file.exists():  # car edges are the strong stable edges below the horizon; the scene has none
        stable = np.load(edges_file).astype(np.float32)
        strong = stable > 55
        strong[: height // 2] = False
        report["stable_edges_missed"] = round(float((strong & ~car).sum() / max(1, strong.sum())), 4)
    (out / "mask.json").write_text(json.dumps(report, indent=1), encoding="utf-8")

    tinted = []
    sources = ([Image.open(out / "stable_edges.png").convert("RGB")] if (out / "stable_edges.png").exists() else []) + \
        [Image.open(frames[i]).convert("RGB") for i in (0, len(frames) // 2, len(frames) - 1)]
    for source in sources:
        pixels = np.asarray(source).copy()
        pixels[car] = (pixels[car] * 0.45 + np.array([255, 40, 40]) * 0.55).astype(np.uint8)
        tinted.append(Image.fromarray(pixels).crop((0, round(0.4 * height), width, height)).resize((1200, round(1200 * 0.6 * height / width))))
    sheet = Image.new("RGB", (1200, sum(t.height + 6 for t in tinted)), "white")
    y = 0
    for tile in tinted:
        sheet.paste(tile, (0, y))
        y += tile.height + 6
    sheet.save(out / "check.jpg", quality=88)
    print(f"Wrote {out / 'mask.png'} and {out / 'check.jpg'}: {json.dumps(report)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["prepare", "render"])
    parser.add_argument("--video", help="video name (default: the only video in queries/)")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--margin", type=int, default=4, help="pixels added around the outline")
    args = parser.parse_args()
    videos = sorted({fensch.frame_key(path.name)[0] for path in fensch.query_images()})
    video = args.video or (videos[0] if len(videos) == 1 else None)
    if video is None:
        raise SystemExit(f"Several videos: choose one with --video ({', '.join(videos)}).")
    if args.command == "prepare":
        prepare(video, args.frames)
    else:
        render(video, args.margin)


if __name__ == "__main__":
    main()
