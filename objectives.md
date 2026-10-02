# Objectives

## Goal

Produce an inventory of predefined street equipment for CA Val de Fensch: one record per **physical feature** with its catalogue code and estimated GPS position. Inputs are equirectangular panoramas captured from known positions and headings, plus the catalogue of feature types and their visual descriptions.

The deliverable is the deduplicated feature list. Bounding boxes are an intermediate result. The same object appears in many consecutive frames: frames are about 4 m apart, so a feature within 15 m is visible in roughly 5 to 8 frames.

## Status (2026-10-02)

**Chosen workflow: two stages.**
1. **Stage 1 (category only):** a local, free vision model, `qwen3.5:9b` on Ollama, via `detect_vlm.py`.
2. **Stage 2 (specific type):** done by Claude visually from contact sheets (`typing_sheets.py` → `decisions.json` → `apply_typing.py`).

The full stage-1 run over the 783 frames (batch `ollama-qwen35-all`) was launched on 2026-10-02 (log: `logs/ollama-qwen35-all.log`; about 4–6 h). It finished the same day: 5010 boxes, 2364 features. Stage 2 was then done on its 808 specific-category features (73 sheets): 626 rejected and 182 typed, each with a condition `state` (good / damaged / bad, mapped to the Clavier `Etat`). Result: `analysis/ollama-qwen35-all-typed.json`. See the stage-2 findings below.

**Stage-2 findings on the full run:**
- About 77% of the stage-1 features in these categories were false. Most were the survey car itself: the orange roof beacon and the camera mast were taken for lighting points, the roof rails for barriers, and their reflections on the bonnet for speed bumps. Road crash guardrails (glissières) were also taken for barriers. Masking a fixed car region in `detect_vlm.py` (beacon, mast, roof rails) would remove most of them before stage 2.
- Kept: 77 lighting points (all `ECL_SIE_06`; no solar `ECL_FEN_01` mast seen), 49 + 14 barriers (wooden railings as fallback, red/white `BAR_FEN_02` gates, `BAR_FEN_01` cycle chicanes), 26 `POT_FEN_03` delineators, 5 `BAN_FEN_01` picnic tables, 1 `RAL01`. Stage 1 found only one speed bump.
- Only 2 features are `damaged` and none `bad`. At 1920 px most items are too small to show wear, so `good` mostly means "no visible defect".
- Features are counted after re-localization (179); a long railing is still split into several features.
- The direct categories (trees, grates, manholes…) were not reviewed and have no state yet. Trees alone are 1446 features, many of them duplicates.

**Detectors compared on the first 10 frames**, against `first10-manual` (Claude's visual annotation, which has known speed-bump errors). Boxes are counted when they overlap a manual box by IoU ≥ 0.3 and lie within 15 m:

| Detector | Manual boxes found | Notes | Cost for 783 frames |
| --- | --- | --- | --- |
| Ollama `qwen3.5:9b`, category only, 4 tiles | **59/86 (69%)** | lamps 9/11, trees 7/7, bollards 41/61, hydrants 2/3, speed bumps 0/4; 60 extra boxes (bins, signs) for stage 2 to reject | free; about 4–6 h on an M4 Pro, 24 GB |
| Groq `qwen/qwen3.8-27b`, full codes, 4 tiles | 37–43/86 (43–50%) | invented runs of identical bollard boxes (filtered); bollards often coded `POT_FEN_01` | measured ≈ $34; ≈ $17 with Groq's Batch API, which accepts this model although its docs don't list it |
| Groq `qwen/qwen3.6-27b` | 3/10 on one frame | more false positives, 2× slower | ≈ $50 (not in Groq's price list) |
| Claude Sonnet 5.5 (`detect.py`) | not run (no API key) | | estimate ≈ $27–50, or $14–26 with the Batch API |

**Model-specific findings:**
- **Image budget:** Groq gives every image the same budget of about 780 tokens, so tiling is required. Ollama processes tiles at near-native resolution.
- **Coordinate conventions differ:** Qwen 3.5 on Ollama uses 0–1000 per axis; Qwen 3.8 on Groq uses 0–1000 of the tile's longer side.
- **Ollama 0.35 vs 0.33:** same quality (58 vs 59/86).
- **Higher resolution:** 2880-px panoramas would probably help for small objects (bollards, hydrants) and position precision. To test with 10 frames in `queries_2880/`.

## Status (2026-10-01)

The first end-to-end prototype runs on the first 10 frames (batch `first10-manual`):

`detect.py` → `analysis/<batch>.json` → `localize.py` (positions, merge, exports) → `node build-detection-viewer.mjs` → `detection-viewer.html`

- **Detection:** the batch did not call the API. Its boxes were drawn by Claude in a Claude Code session, which viewed each panorama and wrote responses in the API's JSON format; `detect.py --replay` read them. The real API path (`claude-sonnet-5-5`, single stage, full panorama) is implemented but not yet run: no API key is configured.
- **Result:** 86 detections, 85 within range, merged into 32 features (20 bollards, 4 lighting points, 3 trees, 3 speed bumps, 2 hydrants).
- **Known errors:** the single real hydrant is split in two (its sightings are 3.4 m apart, against a 3 m merge radius), and the speed-bump boxes are rough.
- **Review editor:** the viewer can add, move, resize and delete boxes and change their codes. Corrections are saved as `analysis/<batch>-reviewed.json` and re-localized automatically.
- **Speed bumps:** the prompt now has a dedicated SPEED BUMPS AND PEDESTRIAN CROSSINGS section. Zebra stripes alone no longer count; shark teeth, a ramp or a surface change do; an uncertain subtype gets `low` confidence instead of being omitted. The manual batch predates this rule.
- **Next:** correct `first10-manual` in the editor to get a reference set, run the API path on the same 10 frames, compare the two, then work on cost (step 2).

## Inputs (verified 2026-10-01)

| File | Content |
| --- | --- |
| `Clavier Equipements CA_Val_de_Fensch.json` | Authoritative code list: 44 codes in rubric part `Type`. Also contains parts `Etat` and `Commentaires`, which are not used yet. |
| `categories_to_collect_fr_en.csv` | 19 categories in French and English. The file starts with a UTF-8 BOM, so strip it when parsing. |
| `fensch_image_descriptions.json` | Visual descriptions of the 23 specific models, keyed by code. They were written from `images/<code>.png`. |
| `fensch_bbox_prompt.md` | Detection prompt and output format, including the code, fallback and confidence rules. |
| `queries/` | 783 panoramas, `<video>_f_<frame, 6 digits>_equirect_anonymized.jpg`, 1920 × 960, all from video `20260805_080341_gs010136`. |
| `ncp_gps_frames.json` | 783 entries `{timestamp_ms, latitude, longitude, orientation, images:[{video_name, frame, …}]}`. This is a one-to-one match with `queries/`. `orientation` is the GPS bearing, written as a **math angle** (degrees counter-clockwise from east), so the **compass heading is `90 − orientation`**. With that conversion it matches the bearing between consecutive positions within 3.6° (median); 96 % of frames are within 20°. The panorama centre column faces that heading. **This file is the only source of camera position and heading.** Ignore the GPS tags in the JPEG EXIF. |

Rules on codes:
- Join images to GPS on **(video_name, frame)**, not on the frame number alone. Frame numbers are not unique across videos; only one video is present today, but more may be added.
- Seven categories have specific models plus a fallback ("à classifier") code: `ABR`, `BAN`, `BAR`, `COR`, `ECL`, `POT`, `VEL`.
- Eleven categories have a single direct code: `ARB`, `BAC`, `BOU-E`, `CEN`, `GRI`, `HOR`, `INC`, `ORN`, `PAPV`, `PUB`, `SAN`.
- Speed bumps use the subtypes `RAL01`–`RAL04` and have no fallback code.
- See `fensch_bbox_prompt.md` for the full rules.

Observed from two frames (031463 and 031557):
- The camera is on a car roof. The roof covers the lower part of the panorama, from about row 0.58 straight ahead. On the ground plane, at `h` = 2 m, this hides the ground closer than about 7.5 m ahead.
- The bonnet reflects the scene. Detections inside the vehicle region are reflections or the car itself; mask that region.
- The panorama centre faces the car's front. This was confirmed on the first 10 frames: with heading = `90 − orientation` and no yaw offset, repeated sightings of the same object land within a few metres of each other.

## Pipeline

1. **Feature types.** Done: the categories, codes and descriptions above. Keep the code list in sync with the Clavier JSON.
2. **Detection with a VLM**, prompted from `fensch_bbox_prompt.md`. A script writes `analysis/<batch>.json`.

   The current prototype is `detect.py`:
   - one `claude-sonnet-5-5` call per full panorama (1920 × 960 is within its native resolution, so boxes come back in pixels 1:1);
   - structured JSON output with an enum of the 44 codes;
   - the code rules taken live from `fensch_bbox_prompt.md`;
   - raw responses kept under `analysis/raw/<batch>/`;
   - an estimated cost per image.

   Recommended design for the next iterations:
   - **Tile the panorama.** One pixel of the panorama is 0.1875°, so a 15 cm bollard at 10 m is about 5 px wide. VLMs also downscale their input. Reproject each panorama into overlapping perspective views, for example 8 views of 90° field of view every 45°, restricted to a band around the horizon. Map boxes back to panorama coordinates in the script. Tiling also removes the equirectangular distortion and the left/right seam problem.
   - **Two stages.**
     - Stage (a) finds category boxes: "find all benches, bollards, bins…".
     - Stage (b) classifies each object, comparing it only against the codes of its category. Supply the candidates' descriptions and their `images/<code>.png` reference photos.
     - Only the 7 categories with specific models, plus the speed-bump subtypes, need stage (b). For the 11 direct categories, the stage (a) category already is the code.
     - Run stage (b) **after** steps 3–4 (localization and deduplication), on each merged feature, using its 2–3 best views: the closest and least occluded crops. This makes about 5–7× fewer calls than classifying every detection, and several viewpoints improve accuracy.
     - One pass over 19 categories and 44 codes hurts recall, and trees and manholes flood the output. Fine-grained matching works better on a crop next to a reference image.
   - **Use the model's native box format.** Gemini returns `[ymin, xmin, ymax, xmax]` on a 0–1000 scale. Qwen-VL returns pixels or a 0–1000 scale depending on the version. Convert to normalized `xywh` in code. Asking the model for normalized `xywh` directly, as the prompt does now, lowers box accuracy.
   - **Recommended models (to confirm on the eval set):**
     - Stage (a): **SAM 3**, Meta's open-weights model, run locally on the tiles and used as a text-prompted box detector ("bollard", "bench"…).
       - It returns a box and a score for every instance; the masks are optional and can be ignored.
       - The scores let you tune recall.
       - Masks only help where a box's bottom centre is not the ground-contact point, for example a tree trunk under a wide canopy.
       - Baselines to compare: Grounding DINO and OWLv2, which are box-only detectors.
       - For vague categories (`ORN`, `PUB`, `PAPV`, `RAL`), check recall. If it is poor, add a pass with a grounding VLM (Gemini or Qwen3-VL) for those categories only.
     - Stage (b): **Claude Opus 5.5** (`claude-opus-5-5`, $4 / $20 per Mtok), compared against Claude Sonnet 5.5 (`claude-sonnet-5-5`, $2 / $10).
       - Use structured output with an `enum` of the category's codes.
       - Put the reference images and descriptions in a cached prefix for each category.
       - Use the Batch API (−50 %) and a low/medium effort.
   - Box bottoms drive the distance estimate, so stage (a) box quality matters more than usual.
   - **Mask the vehicle region.** Tell the model to ignore reflections on the car body.
   - Use temperature 0 and structured JSON output. Keep the raw responses, and record the model and prompt version in each batch.
   - **Evaluate before the full run.** Hand-label 30–50 frames, measure recall and precision per category, and only then run all 783 frames.
3. **Localization.** For each box, compute a ground position:
   - The camera is at height `h` (default 2 m, configurable) and has zero pitch and roll, so the horizon is at row `v = 0.5`. The ground is a horizontal plane.
   - Azimuth: `θ = heading + (u − 0.5) · 360°`, where `heading = 90 − orientation` and `u` is the normalized horizontal position of the anchor point.
   - Elevation: `φ = (0.5 − v) · 180°`. When `φ < 0`, the ground distance is `d = h / tan(−φ)`. When the anchor is at or above the horizon, there is no ground intersection, so leave the position empty.
   - Choose the anchor point by category. Use the bottom-centre of the box for upright objects. Use the box centre for objects lying flat on the ground (`BOU-E`, `GRI`, `RAL*`). For trees, use the trunk base, which is not the bottom of the canopy box. `localize.py` still uses the bottom centre for trees.
   - Do not trust a box bottom that is `truncated` or `occluded` at the base, or that touches the vehicle mask. Flag these boxes or drop them. Nearby objects ahead of or behind the car have their base hidden by the roof. Locate them from frames where they are seen more to the side.
   - **Range gate: drop every detection with `d > 15 m`.** A far object is not lost: it is seen up close from another frame, because frames are less than 5 m apart along the whole track. The only exception is one 54 m gap between frames 011514 and 015863. For `h` = 2 m, 15 m corresponds to a box bottom at row `v ≈ 0.542`. Boxes whose bottom is above that row can therefore be dropped right after stage (a), before any classification call. This saves cost and avoids classifying tiny, distant objects.
   - Expected precision: at 15 m one pixel row is about 0.4 m of range. A 1° pitch error gives about 2 m of error, and a 10 cm error in `h` gives about 0.75 m. Report positions as approximate.
4. **Deduplication across frames.** Merge detections of the same code (or compatible codes) whose estimated positions fall within a tolerance radius into one feature. Ranges are noisy, so bearings from several frames could also be triangulated. Keep the list of source frames and boxes for each feature for review.

   `localize.py` implements this as a greedy merge:
   - nearest sightings first, same category only, within `--merge-radius` (default 3 m);
   - never two detections from the same image in one feature;
   - position is the weighted mean, with weight = confidence / distance²;
   - code by weighted vote.

   Still to tune: a radius per category. Bollards in a row are about 2 m apart, while isolated objects scatter over 3–4 m. Triangulation from bearings is also not done yet.
5. **Viewer.** Done in `detection-viewer.html`:
   - camera positions and headings from `ncp_gps_frames.json`;
   - the full capture track, where clicking an analysed frame opens it;
   - each detection at its estimated position;
   - merged features, highlighted when linked to the current frame;
   - ground status and feature ID in the object details.

   Leaflet and the OSM tiles are loaded online; the bounding-box view works offline.
6. **Export.** Done. `localize.py` writes `exports/<batch>.features.geojson`, `.features.csv` and `.detections.geojson`, and the viewer has export buttons for the features. Still open: whether an import format for the Logiroad Clavier/capture software is also required.

## Open questions

- **Fine yaw offset.** The first 10 frames are consistent with no offset between the panorama centre and the heading, to within about ±5°. Re-check on a longer straight section with well-separated objects.
- **Camera height and mounting.** Confirm the camera height and that the camera is level. The Clavier JSON has `cameraOnBackOfVanMode: false`.
- **Infrastructure.** Stage (a) running locally needs a GPU; an Apple Silicon Mac works but is slow, and a cloud GPU is the alternative. Stage (b) is hosted, so confirm the anonymized images may be sent to an external API. Also decide the acceptable recall and precision.
