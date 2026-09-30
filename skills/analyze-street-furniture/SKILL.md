---
name: analyze-street-furniture
description: Inspect supplied street-scene photographs against this project's Cholet catalogue and write likely item detections with normalized bounding boxes. Use for actual image recognition and localization, not catalogue editing or viewer-only changes.
---

# Analyze street furniture

Paths below are relative to the repository root (two directories above this skill).

Read `llm_description.json` for category definitions, exact names/IDs, diagnostic traits, and known ambiguities. Read `detections.schema.json` for the output contract and the detection section of `README.md` for coordinate conventions. `recognition_prompt.md` provides comparison guidance; its recognition-only response example is superseded by the detection schema for this workflow.

## Inspect and classify

- Analyze only the images or query folder identified by the user. If scope is missing, ask for the query images; do not analyze all catalogue references as if they were new observations.
- Obtain original display-oriented image dimensions and actually inspect every query image. Use original-resolution crops or enlarged views for small caps, rosettes and other discriminators when available. Keep crop offsets and scale factors so coordinates map back to the full displayed image. For EXIF-oriented photographs, use the orientation browsers display and record the corresponding width and height.
- Locate distinct visible benches, barriers, bins, bollards and bicycle stands. Bicycle stands are `potelet`. One object gets one box; do not box each component separately. Include partially visible targets if they can be localized, and mark truncation/occlusion. Do not guess objects fully hidden behind others. Do not return unrelated scene objects unless the user asks for them.
- Compare likely models using structural evidence and their listed confusions. Temporary stickers, paint, backgrounds and wear are weak evidence. Do not invent non-visible material properties, flexibility, dimensions or manufacturer's identity.
- Use `matched` for a supported predefined entry, `category_fallback` for an adequately visible unlisted design within a known category, `ambiguous` for competing plausible models, or `insufficient_evidence` when the view prevents recognition. Use `out_of_scope` only for explicitly requested objects outside the catalogue. Preserve null model fields when unresolved. The visually similar à-gorge and shape-memory à-gorge posts must remain ambiguous unless independent identifying evidence is supplied.
- Confidence labels are qualitative judgments, not calibrated probabilities. Record concise visible evidence, meaningful alternatives and the missing discriminator; do not provide speculative detailed reasoning.

## Boxes and delivery

Use tight axis-aligned boxes enclosing the object's visible extent, including attached arms, base or loop, but excluding shadows and neighbouring objects. Clip at image boundaries; do not extrapolate hidden/full object size. Record `bbox` as `{x,y,width,height}` normalized to [0,1] from the displayed image's top-left. Convert pixel edges using x=left/W, y=top/H, width=(right-left)/W, height=(bottom-top)/H. Round to six decimals, ensuring positive size and right/bottom <=1 after rounding. For crops, first restore full-image coordinates. Mark `bbox_quality` as `approximate` for manually estimated boxes; only use `reviewed` after checking the overlay against the image.

Write valid JSON to `detections/<batch-name>.json`, following `detections.schema.json`. Include every inspected image, including those with an empty `objects` array. Use `analysis_kind: manual_visual` for agent visual inspection, or `model_inference` for actual model-tool output; never label a fixture as real analysis. Record inaccessible inputs as limitations outside the analyzed-image list, not as empty negative results.

Run `node validate-detections.mjs <output>` and resolve errors. Open `detection-viewer.html` and load the JSON plus its images to inspect overlays when browser access is available. If browser access is unavailable, report that overlays have not been visually reviewed and retain `approximate`. Never claim a box is verified just because its coordinates pass validation. Deliver links to the JSON and viewer, and briefly flag material recognition uncertainty.
