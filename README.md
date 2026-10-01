# CA Val de Fensch street-equipment inventory

Tools for detecting catalogue street equipment in equirectangular panoramas, placing it on a map and exporting it. [objectives.md](objectives.md) describes the target pipeline and lists the open questions.

## Data

| Path | Content |
| --- | --- |
| `Clavier Equipements CA_Val_de_Fensch.json` | Catalogue: the 44 equipment codes (rubric part `Type`). |
| `categories_to_collect_fr_en.csv` | The 19 categories to collect, in French and English. |
| `fensch_image_descriptions.json` | Visual descriptions of the 23 specific models, keyed by code. |
| `images/<code>.png` | Reference photo for each described model. |
| `queries/` | 783 panoramas, 1920 × 960, named `<video>_f_<frame:06d>_equirect_anonymized.jpg`. |
| `ncp_gps_frames.json` | Camera position (`latitude`, `longitude`) and `orientation` for each frame. `orientation` is the GPS bearing as a math angle (counter-clockwise from east), so the compass heading is `90 − orientation`. Images are joined on `images[].video_name` and `images[].frame`. This file is the only source of positions; the GPS tags in the JPEG EXIF are ignored. |
| `analysis/` | Detection batches (`<batch>.json`) and raw model responses (`raw/<batch>/`). |
| `exports/` | Merged features and located detections per batch (GeoJSON, CSV). |

## Catalogue browser

Open `index.html` and select one or more Clavier JSON files with **Clavier JSON**. Nothing is embedded or loaded automatically.

For each entry, the page looks in `images/` for a file named after the entry's `code`. It tries `.png`, `.jpg`, `.jpeg`, `.webp`, then `.gif`; for example, `BAN_FEN_01` resolves to `images/BAN_FEN_01.png`. Entries without an image still show their names.

You can search the entries and export the images as a ZIP. The maximum export width is 128 px by default.

To change the page, edit `viewer.template.html`, then run `node build-viewer.mjs`.

## Pipeline

### Chosen workflow: local detection, then typing

1. **Stage 1, local and free:** boxes by category with `qwen3.5:9b` on Ollama (install Ollama ≥ 0.35, then `ollama pull qwen3.5:9b`). The run is resumable; at the end it writes `analysis/<batch>.json`, localizes it and rebuilds the viewer.

   ```bash
   nohup caffeinate -i .venv/bin/python -u detect_vlm.py --batch ollama-qwen35-all > logs/ollama-qwen35-all.log 2>&1 &
   ```

2. **Stage 2, specific types:** contact sheets per category, decisions, then a new typed batch.

   ```bash
   .venv/bin/python typing_sheets.py analysis/ollama-qwen35-all.json
   ```

   Fill `analysis/raw/ollama-qwen35-all/typing/decisions.json`: for each feature, a `code`, `"REJECT"` or `null` (undecided), plus a confidence and evidence. Then:

   ```bash
   .venv/bin/python apply_typing.py analysis/ollama-qwen35-all.json
   ```

   This writes `analysis/ollama-qwen35-all-typed.json`, re-localizes it and rebuilds the viewer.

### Single-stage detectors (kept for comparison)


```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

1. **Detect.** `detect.py` sends each panorama to `claude-sonnet-5-5` with the rules of `fensch_bbox_prompt.md`.
   - It asks for structured JSON: a code from the 44 Clavier codes, pixel box, confidence, occlusion and evidence.
   - It writes `analysis/<batch>.json` in the prompt's output format (normalized `xywh`) and keeps every raw response in `analysis/raw/<batch>/`.
   - It prints an estimated cost per image. An existing batch is never overwritten.

   ```bash
   export ANTHROPIC_API_KEY=...
   .venv/bin/python detect.py --batch first10-sonnet --first 10
   ```

   - `--dry-run` writes the request that would be sent, without calling the API.
   - `--replay DIR` reads saved responses from `DIR/<image stem>.json` instead of calling the API.
   - `--effort` (default `medium`) and `--model` can be changed.
2. **Localize, merge and export.** `localize.py` computes a ground position for every detection, then merges detections into features and writes the exports.

   ```bash
   .venv/bin/python localize.py analysis/first10-sonnet.json
   ```

   - **Position:** it uses the camera position and heading from `ncp_gps_frames.json`, a level camera at `--camera-height` (2 m), a flat ground, and the box's ground-contact point (bottom centre; box centre for manholes, grates and speed bumps).
   - **Range:** detections farther than `--max-range` (15 m) are not placed; the same object is seen closer from another frame.
   - **Merge:** detections of the same category from different frames within `--merge-radius` (3 m) become one feature.
   - **Output:** the batch file is updated in place, and `exports/<batch>.features.geojson`, `.features.csv` and `.detections.geojson` are written.
3. **View and correct.** Run `node build-detection-viewer.mjs`, then open the viewer through `review_server.py` to edit boxes (see below).

Code rules:
- A fallback code means "known category, a design that is not described". It does not mean the object was unclear.
- Objects whose category cannot be established are omitted.
- Speed bumps have no fallback code.
- A zebra crossing alone is not a speed bump; shark-teeth triangles, a ramp or a surface change show one.
- A speed bump that is certain but whose subtype is not gets the likeliest subtype with `low` confidence instead of being omitted.

`fensch_bbox_prompt.md` can also be used by hand with any image-capable model. Its boxes are normalized `xywh` on the displayed image: `x` and `y` are the top-left corner, and every value is in [0,1].

The batch `first10-manual` was not produced by the API. Its boxes were drawn by Claude in a Claude Code session after viewing the first 10 panoramas, then replayed through `detect.py --replay`. It serves as a reference for comparing model runs.

### Trying Qwen on Groq

`try_groq.py` runs the detection rules with a Qwen vision model on Groq (`GROQ_API_KEY` in `.env`, which is git-ignored). It writes replay records that `detect.py --replay` turns into a batch:

```bash
.venv/bin/python try_groq.py --batch groq-qwen38-first10 --start 0 --first 10
```

How it handles the model:
- **Tiles:** Groq gives every image the same ~780-token budget, so the horizon band is sent as 4 overlapping tiles.
- **Coordinates:** the model uses a 0–1000 scale of each tile's longer side on both axes, converted back to panorama pixels.
- **Invented runs:** runs of identical, evenly spaced boxes (invented bollard rows) are filtered out and kept in the record.
- **Re-processing:** `--reparse` rebuilds objects from the saved replies without new API calls.

Measured on the first 10 frames with `qwen/qwen3.8-27b`:
- about 14 s and 27.5k input + 5.3k output tokens per frame;
- 43% of the manual boxes recovered (lamps 55%, trees 71%, bollards 41%);
- many extra boxes, bollards often coded `POT_FEN_01`.

## Detection viewer

Run `node build-detection-viewer.mjs`, then start the review server and open the viewer:

```bash
python3 review_server.py
```

Then open http://127.0.0.1:8765/detection-viewer.html. Opening `detection-viewer.html` directly also works for viewing, but saving edits then falls back to a download.

On screens at least 900 px wide the viewer fills the window:
- the panorama is at the top; *Fit* shows it whole;
- the map is below, and the bar between them can be dragged to resize it (double-click resets);
- objects and the edit form are on the right.

Selection is shared between boxes, list rows and map markers, and hovering any of them highlights the same object in the other two. Narrower screens use a stacked, scrolling layout.

◀ ▶ above the image move between the images of the current batch, with a position counter. Page Up and Page Down do the same, and so do the ← → keys outside edit mode; in edit mode the arrows nudge the selected box.

**Street View ↗** (next to ◀ ▶) opens Google Street View in a separate browser window that you can move and resize, at the frame's GPS position.
- **Direction:** the selector next to the button chooses the frame heading or the selected object. It returns to the frame heading whenever the frame changes.
- **Following:** while the window is open, it follows the viewer. Changing frame (◀ ▶, Page Up/Down, track points), selecting another object, or changing the direction updates it.
- **Different imagery:** Google shows its nearest panorama, taken on another date, so objects may differ.
- **Privacy:** the frame's position is sent to Google only once you open the window.
- **Pop-ups:** if the browser blocks the window, allow pop-ups for the page.
- **URL format:** the documented Maps URLs format (`map_action=pano`).

### Correcting boxes

Tick **Edit boxes**:
- **Draw box** (or the N key), then drag on the image to add a box with the selected *New box code*.
- Drag a box to move it, or drag a corner to resize it.
- The side panel changes the code (the 44 Clavier codes grouped by category), confidence, occluded/truncated flags and evidence. Its **Delete box** button removes the box, as does the Delete key.
- Arrow keys nudge the selected box by 1 px (Shift: 10 px). Esc cancels.
- **Zoom** (up to 6×) helps with small bollards.

Edits are kept in this browser until you save. **Save review** writes `analysis/<batch>-reviewed.json`; the original batch is never modified. It then recomputes positions with `localize.py`, rebuilds the viewer and reloads it. Edited and added boxes are marked (dashed outline, tag in the list), and deleted boxes are listed in each image's `review.deleted`.

Without the review server, Save downloads the JSON instead: put it in `analysis/`, then run `localize.py` and the build.

The build:
- embeds every `analysis/*.json` file;
- pairs each `images[].image_file` with a file in `queries/`. The build fails if one is missing.

Use `--watch` to rebuild when `analysis/` or `queries/` changes, then refresh the browser.

The bounding-box view works offline. The map panel loads Leaflet and OpenStreetMap tiles from the internet. It shows:
- the capture track (click an analysed frame to open it);
- the camera and its heading;
- each detection at its estimated position;
- the batch's merged features.

The object details give the distance, bearing and feature ID. Two buttons export the merged features as GeoJSON or CSV.

To change the viewer, edit `detection-viewer.template.html`, never the generated HTML.

## Legacy Cholet tooling

The repository started as a Cholet catalogue. The following files still use that contract (`banc`/`barriere`/`corbeille`/`potelet`, `llm_description.json`) and do not work with the Fensch data until they are ported:
- `validate-detections.mjs`
- `detection-core.js`
- `build-llm-description.mjs`
- `recognition_prompt.md`
- `examples/detections.example.json`
- `skills/analyze-street-furniture/`

Their Cholet inputs have been deleted. `jpeg-exif.mjs` is no longer used: positions come from `ncp_gps_frames.json`.

## Requirements

Node.js builds the pages (no npm dependencies). Python 3.10+ with `requirements.txt` (Anthropic SDK, Groq SDK, Pillow) runs the pipeline; stage 1 also needs Ollama ≥ 0.35 with `qwen3.5:9b`.

```bash
node build-viewer.mjs
node build-detection-viewer.mjs
```
