# Change log

Notable changes to the CA Val de Fensch inventory pipeline, newest first. Details of the process are in `claude.md`, and status and findings are in `objectives.md`.

## 2026-10-02

### Process
- **Step-by-step process with a runner.** `run_pipeline.py` chains the steps on a range of frames: car mask, stage 1, localization, stage-2 sheets, then (after the visual decisions) apply. Every step is resumable, and `--status` shows what is done.
- **Car mask (`car_mask.py`, new step 1).** The camera is fixed on the car, so one mask serves a whole video.
  - It is traced once from the median and stable-edge images of 60 frames. It can't be fully automatic, because the glossy body reflects the scene.
  - Stage 1 paints the car grey before the model sees the tiles, and drops boxes that lie mostly on the car.
  - `localize.py` flags a ground-contact point on the car as unreliable.
  - On the full run it would have removed 485 of the 497 features rejected as car parts, and none of the 156 real objects.
- **First 100 frames rerun** with the mask (batch `first100`).

### Detection rules
- **J11 flexible delineators** are road signalling, not bollards, so they are ignored. `POT_FEN_03` (SOLIDOR) is kept, but only when its ribbed lower shaft and flared base are visible. The 26 white cycle-lane posts typed `POT_FEN_03` in the full run are now rejected.
- **Public equipment only.** Anything on private property is ignored. Only trees the city probably maintains count: street trees, and trees in squares, public parks and public car parks. This applies to the detection prompt, the stage-1 prompt and stage 2.

### Viewer and positions
- **Manual map positions.**
  - `ground` stays the automatic position computed from the box. `manual_position` is what the map shows and what gets exported.
  - It starts as a copy of the automatic one, and once moved it is kept (`edited: true`).
  - A feature sits at the mean of its moved detections.
  - Exports add `position_edited`, `auto_latitude` and `auto_longitude`.
- **Map editing.**
  - Drag a selected marker, or any marker in edit mode. Moving a feature moves all its detections.
  - Shift+click, Shift+drag or the "Select box" tool build a selection. It moves by mouse or with the arrow keys (0.2 m per press, 1 m with Shift).
  - A grey ring marks the automatic position, and edit mode offers a reset.
- **Select from the map.** Clicking a feature selects its object, opening the frame where it is seen closest.
- **Rear view (R):** shows only the 180° behind the car.
- **D deletes** the selected box in edit mode.
- **Fixes:** markers were re-ordered on hover, so the browser sent clicks to the wrong element. The map now gets a view before markers are added.

### Stage 2 and results
- **Condition state.** Each typed object gets `good`, `damaged` or `bad`, mapped to the Clavier `Etat` field (Bon / Moyen / Mauvais), and exported per feature as `state` and `etat`.
- **Full stage-1 run** `ollama-qwen35-all`: 783 frames, 5010 boxes, 2364 features.
- **Its stage-2 typing:** 808 features decided, 652 rejected (mostly the car itself and road guardrails) and 156 typed. Result: `ollama-qwen35-all-typed`.
- **Two-stage detection adopted:** local Ollama `qwen3.5:9b` finds categories, then Claude types them visually from contact sheets (`typing_sheets.py`, `apply_typing.py`).

## 2026-10-01
- **Viewer workspace layout**, with the image and the map linked.
- **First end-to-end prototype** on the first 10 frames, covering detection, localization (positions, merge, exports) and the review viewer with box editing. The detections were Claude's manual visual annotation (`first10-manual`).
- **Repository migrated** from the earlier Cholet catalogue.
