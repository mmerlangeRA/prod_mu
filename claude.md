# CA Val de Fensch street-equipment inventory

The goal is to detect catalogue equipment in equirectangular street panoramas, estimate each item's GPS position, show the results on a map, and export them. The pipeline, verified input facts and open questions are in [objectives.md](objectives.md). Read that file before starting pipeline work.

The repository was migrated from an earlier Cholet catalogue. The Cholet sources (`Clavier Mobilier Urbain Cholet.json`, `llm_description.json`, `mobilier-urbain-cholet-images-128px/`, `AGENTS.md`) have been deleted. Several tools still expect them; see "Legacy tooling".

## Sources

- `Clavier Equipements CA_Val_de_Fensch.json`: authoritative list of the 44 codes. Preserve it.
- `categories_to_collect_fr_en.csv`: the 19 categories to collect, in French and English.
- `fensch_image_descriptions.json`: visual descriptions of the 23 specific models, keyed by code.
- `images/<code>.png`: one reference photo per described model. These are not query scenes.
- `fensch_bbox_prompt.md`: the current detection prompt. It is the authoritative source for code rules (specific model / category fallback / direct category / speed-bump subtype), bbox conventions and the output JSON format.
- `queries/`: 783 equirectangular panoramas (1920 × 960) named `<video>_f_<frame:06d>_equirect_anonymized.jpg`.
- `ncp_gps_frames.json`: lat/lon and `orientation` for each frame. `orientation` is the GPS bearing as a math angle (counter-clockwise from east), so the **compass heading is `90 − orientation`** (`fensch.orientation_to_heading`). Never use `orientation` directly as a compass bearing. This file is the **only** source of camera position and heading; do not use the GPS tags in the JPEG EXIF (`jpeg-exif.mjs` is no longer used). Join on (video_name, frame).
- The camera is on a car roof. The roof and bonnet cover the lower part of each panorama and reflect the scene, so ignore detections in that region.

## Code rules

The full rules are in `fensch_bbox_prompt.md`. In short:
- A fallback code (`ABR_FEN_01`, `BAN_CHO_01`, `BAR_CHO_03`, `COR_CHO_01`, `ECL_SIE_06`, `POT_CHO_04`, `VEL_FEN_06`) means "the category is clear, but the design is not one of the described models". It is not a label for blur or uncertainty. If even the category is uncertain, omit the object.
- Bicycle racks and shelters have their own category (`VEL_FEN_*`). They are not bollards.
- Speed bumps have no fallback code. A zebra crossing alone is never a speed bump. Shark-teeth triangles, a ramp or a surface change across the lane are evidence of one. When a speed bump is certain but its subtype is not, choose the likeliest subtype with `low` confidence instead of omitting it. Details: SPEED BUMPS AND PEDESTRIAN CROSSINGS in `fensch_bbox_prompt.md`.
- Never invent, translate or concatenate codes. Never infer detections from filenames, location or expected inventory.

## Pipeline

Python code needs the local virtualenv: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`. Shared helpers:
- `fensch.py`: catalogue, GPS and geometry;
- `vlm_tools.py`: tiling, box parsing and post-filters for vision-model scripts.

### Chosen workflow: two stages

**Stage 1, local and free (category only): `detect_vlm.py`.**
- **Model:** `qwen3.5:9b` on Ollama (≥ 0.35), returning the 19 English category names. Each frame is sent as 4 overlapping tiles over the horizon band.
- **Coordinates:** boxes are on a 0–1000 scale **per axis** of each tile. Groq's Qwen 3.8 instead uses the tile's *longer* side (see `try_groq.py`).
- **Filters:** duplicates from overlapping tiles are merged; invented runs of identical, evenly spaced boxes are dropped (`vlm_tools.repetition_runs`); boxes lying on the car are dropped (`on_vehicle`).
- **Resumable:** progress is saved frame by frame in `analysis/raw/<batch>/`, so re-running the same command continues where it stopped. At the end it writes `analysis/<batch>.json` and runs `localize.py` and the viewer build.
- **Codes:** categories with specific models get their fallback code, and speed bumps the placeholder `RAL01`, all with `code_status: "category_only"`. Direct categories get their final code.
- **The full run (`ollama-qwen35-all`, 783 frames)** was launched on 2026-10-02, detached with `caffeinate`, logging to `logs/ollama-qwen35-all.log`. To check it: `tail -3 logs/ollama-qwen35-all.log`. If it stopped, re-run:

  `nohup caffeinate -i .venv/bin/python -u detect_vlm.py --batch ollama-qwen35-all > logs/ollama-qwen35-all.log 2>&1 &`
- **Measured on the first 10 frames** (against `first10-manual`): 69% of manual boxes found (lamps 9/11, trees 7/7, hydrants 2/3, bollards 41/61), about 20–30 s per frame. **Speed bumps were not found (0/4)**, and there are extra boxes (wheelie bins, signs, private planters) that stage 2 must reject.

**Stage 2 (specific type), done by Claude visually in a dedicated session:**
1. `.venv/bin/python typing_sheets.py analysis/ollama-qwen35-all.json` writes, in `analysis/raw/<batch>/typing/`:
   - contact sheets per category (`POT_01.jpg`…): numbered crops of each merged feature's best views (closest first) with the detected box in red, under that category's reference photos and codes;
   - `index.json`;
   - a `decisions.json` template. An existing one is never overwritten, so the work can resume.
2. Read `fensch_image_descriptions.json` for the category's codes. Then, for each sheet, view the image and fill `decisions.json` for every item:
   - **`code`:** the specific model when the description is visually supported; otherwise the category fallback; or another category's code when the category is wrong (e.g. a bicycle rack taken for a bollard); or `"REJECT"` for non-equipment (wheelie bins, private bins, signs, reflections, car parts). `null` means undecided.
   - **`confidence`:** high, medium or low.
   - **`evidence`:** 1–2 short visible reasons.
   - Small distant objects of the right category keep the fallback with `low` confidence; do not reject them.
   - Stage-1 boxes can be a little off, and neighbouring bollards can be merged into one feature: judge the object nearest the red box in the clearest view.
   - Speed bumps: choose the subtype with the rules in `fensch_bbox_prompt.md`, and reject zebra-only crossings.
3. `.venv/bin/python apply_typing.py analysis/ollama-qwen35-all.json` writes `analysis/<batch>-typed.json` (the source batch is untouched), re-localizes it and rebuilds the viewer.
4. Quota: this stage uses the Claude Code plan (the `get_usage` tool shows it). Measure the usage of the first 2 sheets before doing all of them; a fresh session keeps each action cheap.

### Other detectors (kept for comparison)

1. `detect.py --batch NAME --first N` calls `claude-sonnet-5-5` on each panorama and writes `analysis/NAME.json`, with raw responses in `analysis/raw/NAME/`.
   - It needs `ANTHROPIC_API_KEY`. Never write a key into a file.
   - `--dry-run` writes the request without calling the API.
   - `--replay DIR` reads saved responses instead of calling the API.
2. `try_groq.py` runs Qwen on Groq. It reads `GROQ_API_KEY` from the git-ignored `.env`, never printed.
   - Features: tiles, long-side coordinates, repetition filter, `--reparse` to rebuild from saved replies, and `--batch-api` (50% cheaper, with a `--max-usd` guard).
   - Results on the first 10 frames are the `groq-qwen38-*` batches.
3. `localize.py analysis/NAME.json` adds camera, `ground` and `features` to the batch and writes `exports/NAME.*`. It is idempotent.
4. `node build-detection-viewer.mjs` rebuilds the viewer.

To view and edit, run `python3 review_server.py` (the `review-server` entry in `.claude/launch.json`) and open http://127.0.0.1:8765/detection-viewer.html. A `file://` preview in the app pane cannot load the relative image paths.

The viewer's edit mode adds, moves, resizes and deletes boxes and changes their codes. Edits are kept in the browser's localStorage until saved. **Save** sends the batch to `review_server.py`, which:
- validates codes and boxes;
- writes `analysis/<batch>-reviewed.json`, never the original batch (a `-reviewed` file is a working copy that each save replaces);
- runs `localize.py` on it and rebuilds the viewer.

The viewer's **Street View ↗** button opens the documented Maps URLs pano view (`map_action=pano&viewpoint=…&heading=…`) in a separate named window, which then follows frame and selection changes. Open it only on user request, because it sends the position to Google.

Reviewed objects carry `review: "added" | "edited"`, and each image keeps `review.deleted`. Treat reviewed batches as the human-corrected reference when evaluating model runs.

When you stand in for the API by annotating images yourself:
- write responses in the `detect.py` response format, with `"source": "manual_visual"`;
- run them through `--replay`;
- name the batch so it is clearly manual (for example `first10-manual`).

## Outputs

- Detection batches go in `analysis/<batch-name>.json`, in the output format of `fensch_bbox_prompt.md`. Give each batch a distinct name and never overwrite an earlier batch. Every `image_file` must exist in `queries/`, or `node build-detection-viewer.mjs` fails.
- `detection-viewer.html` is generated by `node build-detection-viewer.mjs` (add `--watch` to rebuild on changes). It embeds the analysis JSON and references the images in `queries/` by relative path. Edit `detection-viewer.template.html`, never the generated file.
- `index.html` (built from `viewer.template.html` by `node build-viewer.mjs`) browses a Clavier JSON loaded by the user, using `images/<code>.png`.

## Viewers

Bounding-box viewing must work offline and without dependencies. The only online parts are Leaflet (loaded from unpkg) and the OSM tiles for the map panel. When they cannot load, the map panel must degrade gracefully. Render all input text as text, never as HTML.

## Legacy tooling (Cholet contract, not yet ported)

Do not use the tools below for Fensch results until they are ported. They use the Cholet categories (`banc`/`barriere`/`corbeille`/`potelet`), the `decision`/`model_name`/`model_id` fields, and `llm_description.json`:
- `validate-detections.mjs` and `detection-core.js`: the validator fails with ENOENT because `llm_description.json` is missing. The current detection viewer does not use `detection-core.js`.
- `build-llm-description.mjs`: reads the deleted Cholet JSON. Do not run it.
- `recognition_prompt.md`, `skills/analyze-street-furniture/SKILL.md`, `examples/detections.example.json`.
- `detections.schema.json`, `detection-core.test.cjs` and `detections/` are referenced in places but do not exist.

Until a Fensch validator exists, check outputs against the rules in `fensch_bbox_prompt.md`: codes from the Clavier list, `classification` consistent with the code, and bboxes within [0,1].

## Working rules

- To analyze query images yourself, view them with the image tools and follow `fensch_bbox_prompt.md`. If the images cannot be viewed, say so instead of producing detections. Generated images are not evidence.
- Do not claim browser or visual validation unless you actually performed it.
- The heading conversion (`90 − orientation`) and the panorama centre facing forward were checked against the GPS track and against repeated sightings. The camera height (2 m) and the fine yaw offset are still unverified (see the open questions in `objectives.md`).
