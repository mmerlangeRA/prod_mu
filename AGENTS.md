# Street-furniture catalogue and image detection

This project contains the Cholet street-furniture catalogue, visual model descriptions, and offline HTML viewers.

## Skills

For requests to inspect query photographs, identify likely catalogue items, or produce bounding boxes, read and follow [the local analyze-street-furniture skill](skills/analyze-street-furniture/SKILL.md). This repository-local skill is linked explicitly here; it does not require installation into a user's global skills directory.

For changes to the viewers or documentation, use ordinary code editing; the image-analysis skill applies only when actual photographs need analysis. Use existing image inspection tools to view supplied photographs. Generated images are not evidence for recognition.

## Sources and outputs

- `Clavier Mobilier Urbain Cholet.json`: original catalogue, including embedded reference images. Preserve it.
- `llm_description.json`: exact model names/IDs, category definitions, visual descriptions and uncertainty rules.
- `images/`: original reference photographs and fallback icons, not a folder of query scenes.
- `recognition_prompt.md`: model comparison guidance. Detection tasks use `detections.schema.json` as the output format instead of the simpler recognition-only response format.
- `detections/<batch-name>.json`: default location for new analysis results. Give each batch a distinct name and avoid overwriting earlier analyses.
- `detection-viewer.html`: offline bounding-box viewer.

`a classifier` means an adequately visible object in a known category whose design is outside the predefined list. It does not mean blur, ambiguity or missing evidence. Bicycle stands belong to `potelet` in this catalogue.

## Editing and checks

- Edit `viewer.template.html` for the catalogue page and run `node build-viewer.mjs`.
- Edit `detection-viewer.template.html` or `detection-core.js` for the bounding-box viewer and run `node build-detection-viewer.mjs`.
- Edit curated descriptions in `build-llm-description.mjs`, then run it to regenerate `llm_description.json`. Do not regenerate descriptions merely to analyze a query image.
- Validate detection output using `node validate-detections.mjs detections/<batch-name>.json`.
- Run `node --test detection-core.test.cjs` when changing the detection contract, validator or overlay coordinate calculations.
- Keep viewers offline and dependency-free. Render input text as text, not HTML. Never infer recognition results just from filenames or catalogue labels.

When images cannot be accessed or viewed, report that limitation and request the missing inputs; do not fabricate detections. Do not claim browser or visual validation unless it was actually performed.
