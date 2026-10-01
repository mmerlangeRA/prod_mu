# Cholet street-furniture catalogue

Offline tools for exploring reference images and reviewing likely objects detected in query photographs.

## Catalogue

Open `index.html` in a browser, then select one or more files using **Clavier JSON**. The page starts empty: no catalogue data is embedded or loaded automatically. Selected files replace the catalogue for the current session only. Thumbnail fields are ignored. For each entry, the page looks in `images/` for a file whose basename exactly matches the entry's `code`, trying `.png`, `.jpg`, `.jpeg`, `.webp`, then `.gif`. For example, code `BAN_FEN_01` resolves to `images/BAN_FEN_01.png` when that file exists. Entries without a matching image still display their names and values. Search the loaded entries, inspect their images, and export a named image ZIP. The maximum export width defaults to 128 px; aspect ratio and transparency are preserved, and smaller images are not enlarged.

`llm_description.json` groups the 32 physical model references and four fallback icons into `banc`, `barriere`, `corbeille`, and `potelet`. Each model has detailed visual descriptions, discriminating features, viewpoint guidance and known confusions. Bicycle stands are included under `potelet`.

An `a classifier` entry means **known category, design outside the predefined models**. It is distinct from an uncertain identification. Generic names such as `banc_banc` are specific reference entries, not fallback labels.

## Ask an agent to analyze photographs

Place query photographs in a folder of your choice, then ask, for example:

> Read AGENTS.md and use skills/analyze-street-furniture/SKILL.md. Analyze all photographs in query-images/. Identify likely street-furniture items using llm_description.json, including partially visible objects. Write detections/inspection-01.json according to detections.schema.json with one bounding box per object, evidence, qualitative confidence, and alternatives where uncertain. Validate the JSON and inspect the overlays if browser access is available.

The agent must view the photographs, not infer results from filenames. If no photographs have been supplied, it should ask for them. This repository does not contain an automatic detection backend; the viewer displays results produced by an image-capable model or visual analysis. `recognition_prompt.md` can also be used with another image-capable model. For bounding-box tasks, require `detections.schema.json` instead of that prompt's simpler recognition-only output.

## View bounding boxes

Place analysis JSON files in `analysis/` and referenced query images in `queries/`, then run `node build-detection-viewer.mjs`. The builder first matches each `images[].image_file` to an exact filename in `queries/`; for a legacy single-image JSON with a generic internal filename, it falls back to an image sharing the analysis JSON's basename. Batch JSON files containing multiple `images[]` entries are supported. Open **`detection-viewer.html`** directly in a browser; no server or installation is required. The generated viewer embeds the analysis JSON but keeps query images as relative file references.

The viewer also reads JPEG EXIF GPS, capture time, orientation and heading during the build. For equirectangular panoramas, it estimates each detection's compass bearing from the horizontal centre of its bounding box, using `GPSDestBearing` as the panorama centre heading (and `GPSImgDirection` as a fallback). The Leaflet map projects detections along those bearings at an adjustable nominal distance; these are approximate display positions because the images contain no object-depth measurement. Leaflet and OpenStreetMap tiles are loaded from the internet, while bounding-box viewing continues to work offline.

For a drop-and-refresh workflow, start `node build-detection-viewer.mjs --watch` once and leave it running. It rebuilds the HTML whenever `analysis/` or `queries/` changes; refresh the browser after a successful rebuild. Without the watcher, run the one-off build command after adding files. A static browser page cannot enumerate local folders on refresh by itself.

1. Choose a detection JSON file.
2. Choose the query image files (multiple selection), or use the image-folder picker.
3. Select an image and click a box or result row to inspect the evidence and model alternatives. Toggle labels, filter by category or decision, or zoom to examine details.

Files are read locally and never uploaded. Match images using `image_file` relative paths, with a unique basename as a fallback. If two uploaded images share the same name, use the folder picker to preserve paths. Missing files and image-dimension mismatches are reported. A mismatch blocks boxes rather than placing them incorrectly.

The viewer initially shows a clearly labelled **illustrative example** using the existing Bercy reference image. This is a demonstration box, not a new detection run. Keep the repository's `images/` directory beside the viewer for this example; imported query photographs are chosen manually and need not reside beside it.

## Detection JSON contract

See `detections.schema.json` and `examples/detections.example.json`. Required top-level fields:

- `schema_version`: `1.0.0`
- `coordinate_system`: `normalized_xywh`
- `analysis_kind`: `manual_visual`, `model_inference`, or `illustrative_example`
- `images`: inspected images, each with `image_id`, `image_file`, original displayed `width` and `height`, and an `objects` array

Each object includes a unique per-image `object_id`, category, decision, exact catalogue model name/ID or null, qualitative category/model confidence, a `bbox`, box quality, occlusion/truncation flags, evidence and alternative names.

Coordinates use the **display-oriented** image (EXIF rotation applied): origin at the top-left, x grows right, y grows down. Boxes enclose the visible object extent, excluding shadows, and are clipped to the image. `x` and `y` are the upper-left corner; `width` and `height` are positive fractions of image dimensions. All values lie in [0,1], and x+width and y+height must not exceed 1. For a 1000 × 800 image, pixel edges (100,160)–(400,640) become `{ "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.6 }`.

Use `bbox_quality: approximate` for estimated boxes and `reviewed` only after visually checking their overlays. An empty `objects` array is a valid negative result for an inspected image. Do not use it for inaccessible/uninspected images.

| Decision | Model fields | Meaning |
| --- | --- | --- |
| `matched` | Exact predefined name and ID | Visible evidence supports that model |
| `category_fallback` | Exact category fallback name and ID | Visible design is outside the predefined list |
| `ambiguous` | null | Several models remain plausible |
| `insufficient_evidence` | null | Visibility prevents a decision |
| `out_of_scope` | null; category also null | Requested object is outside the catalogue |

Model confidence is null for unresolved decisions. Confidence labels are not measured probabilities. The validator checks names, IDs, categories, decision consistency, unique IDs and box bounds; it cannot prove recognition accuracy or box placement.

## Maintenance

Node.js is needed only to rebuild generated pages/data and run checks. No npm dependencies are required.

```powershell
node build-viewer.mjs
node build-llm-description.mjs
node build-detection-viewer.mjs
node validate-detections.mjs examples/detections.example.json
node --test detection-core.test.cjs
```

`viewer.template.html` and `detection-viewer.template.html` are editable page sources. `detection-core.js` shares validation and coordinate logic between the viewer and CLI. `build-llm-description.mjs` contains the curated descriptions and extracts the original reference PNGs to `images/`. Preserve the original catalogue JSON. Project-specific agent instructions and skill links are in `AGENTS.md`.
