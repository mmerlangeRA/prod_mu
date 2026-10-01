"""Helpers shared by the VLM detection scripts (Groq and Ollama): tiling, box parsing and post-filters."""
import base64
import io
import json
import re

BAND = (0.229, 0.792)  # fraction of the panorama height sent to the model: horizon band, without sky and most of the car


def tiles(width, height, count):
    """Overlapping (x0, y0, x1, y1) tiles over the horizon band."""
    tile_width = round(width / count * 1.25)
    step = (width - tile_width) / (count - 1) if count > 1 else 0
    top, bottom = round(BAND[0] * height), round(BAND[1] * height)
    return [(round(i * step), top, round(i * step) + tile_width, bottom) for i in range(count)]


def tile_corners(obj):
    """x_min, y_min, x_max, y_max as numbers; None when malformed.

    Besides the schema's four numbers, accepts the shapes the model produces when it drifts: Qwen's native
    "bbox_2d" list, the whole [x1, y1, x2, y2] box put into x_min, or two corner points x_min=[x1, y1], x_max=[x2, y2].
    """
    def numbers(value):
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            return None
    if isinstance(obj.get("bbox_2d"), list):
        values = numbers(obj["bbox_2d"])
    elif isinstance(obj.get("x_min"), list) and len(obj["x_min"]) == 4:
        values = numbers(obj["x_min"])
    elif isinstance(obj.get("x_min"), list) and isinstance(obj.get("x_max"), list) and len(obj["x_min"]) == len(obj["x_max"]) == 2:
        values = numbers(obj["x_min"] + obj["x_max"])
    else:
        values = numbers([obj.get(k) for k in ("x_min", "y_min", "x_max", "y_max")])
    if not values or len(values) != 4:
        return None
    x0, y0, x1, y1 = values
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def repetition_runs(objects, min_run=5, size_tolerance=1.2, step_tolerance=0.2):
    """Indices of invented runs: >= min_run boxes of one code with near-identical sizes and regular spacing.

    Real rows recede in perspective, so their spacing grows (steps 7 -> 21 px on a real bollard row); invented runs keep
    a constant step (12 px every time on Groq's Qwen 3.8). step_tolerance 0.2 separated the two on the first 10 frames.
    """
    flagged = set()
    by_code = {}
    for index, obj in enumerate(objects):
        by_code.setdefault(obj["code"], []).append(index)
    for indices in by_code.values():
        if len(indices) < min_run:
            continue
        indices.sort(key=lambda i: (objects[i]["box"][0] + objects[i]["box"][2]) / 2)
        widths = [objects[i]["box"][2] - objects[i]["box"][0] for i in indices]
        heights = [objects[i]["box"][3] - objects[i]["box"][1] for i in indices]
        # Grow runs of consecutive boxes (left to right) whose sizes stay within the tolerance of the run's first box.
        start = 0
        while start < len(indices):
            end = start + 1
            while end < len(indices) and max(widths[start], widths[end]) <= size_tolerance * min(widths[start], widths[end]) \
                    and max(heights[start], heights[end]) <= size_tolerance * min(heights[start], heights[end]):
                end += 1
            run = indices[start:end]
            if len(run) >= min_run:
                centres = [(objects[i]["box"][0] + objects[i]["box"][2]) / 2 for i in run]
                steps = [b - a for a, b in zip(centres, centres[1:])]
                mean = sum(steps) / len(steps)
                spread = (sum((step - mean) ** 2 for step in steps) / len(steps)) ** 0.5
                if mean > 0 and spread <= step_tolerance * mean:
                    flagged.update(run)
            start = end
    return flagged


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / union if union else 0.0


def parse_json(text):
    """Merge the "objects" of every JSON object in the reply; models sometimes add text or a second JSON block."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    decoder, position, objects, found = json.JSONDecoder(), 0, [], False
    while (start := text.find("{", position)) != -1:
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            position = start + 1
            continue
        if isinstance(value, dict):
            found = True
            objects.extend(value.get("objects", []))
        position = end
    if not found:
        raise ValueError("no JSON object in the response")
    return {"objects": objects}


def tile_urls(image, count):
    for x0, y0, x1, y1 in tiles(image.size[0], image.size[1], count):
        buffer = io.BytesIO()
        image.crop((x0, y0, x1, y1)).save(buffer, "JPEG", quality=92)
        yield (x0, y0, x1, y1), "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
