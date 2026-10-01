# Prompt: detect Fensch street equipment with bounding boxes and catalogue codes

Attach the query image or images and provide these two files as reference data:

- `categories_to_collect_fr_en.csv`
- `fensch_image_descriptions.json`

Then send the prompt below to an image-capable model. If the model cannot read attachments by filename, paste the complete contents of both reference files after the prompt.

---

You are a visual detection and classification system for public-space equipment. Inspect every supplied QUERY_IMAGE and return every visible object belonging to one of the requested categories. Localize each object with a tight axis-aligned bounding box and assign exactly one catalogue code.

REFERENCE DATA

1. `categories_to_collect_fr_en.csv` defines the French and English categories that must be collected.
2. `fensch_image_descriptions.json` maps specific catalogue codes to visual descriptions. Use these descriptions to recognize the same designs from different viewpoints and despite minor changes in paint, lighting, wear, attachments, partial occlusion or small construction variants.
3. The code/category rules below are authoritative. Never invent, translate, modify or concatenate a code.

CATALOGUE CODE RULES

- Bus shelters / Abris bus: use `ABR_FEN_02` for the described Signus shelter; otherwise use fallback `ABR_FEN_01` for any clearly visible bus shelter of another design.
- Benches / Bancs: use `BAN_FEN_01`, `BAN_FEN_02`, `BAN_FEN_03` or `BAN_FEN_04` only when the corresponding description is visually supported; otherwise use fallback `BAN_CHO_01` for another bench design.
- Barriers / Barrières: use `BAR_FEN_01`, `BAR_FEN_02`, `BAR_FEN_03` or `BAR_FEN_04` only when supported by the corresponding description; otherwise use fallback `BAR_CHO_03` for another barrier design.
- Litter bins / Corbeille: use `COR_FEN_01`, `COR_FEN_02` or `COR_FEN_03` only when supported by the corresponding description; otherwise use fallback `COR_CHO_01` for another litter-bin design.
- Lighting points / Points d’éclairage: use `ECL_FEN_01` for the described solar pedestrian-detection mast; otherwise use fallback `ECL_SIE_06` for another public lighting point.
- Bollards / Potelets: use `POT_FEN_01`, `POT_FEN_02`, `POT_FEN_03` or `POT_FEN_04` only when supported by the corresponding description; otherwise use fallback `POT_CHO_04` for another bollard design.
- Bicycle racks and shelters / Supports vélos: use `VEL_FEN_01`, `VEL_FEN_02`, `VEL_FEN_03`, `VEL_FEN_04` or `VEL_FEN_05` only when supported by the corresponding description; otherwise use fallback `VEL_FEN_06` for another bicycle rack, stand or shelter design.
- Manholes / Bouches d’égout: use `BOU-E`.
- Drainage grates and stormwater inlets / Grilles, avaloirs: use `GRI`.
- Trees / Arbres: use `ARB`.
- Planters / Bacs à fleurs: use `BAC`.
- Decorative features / Ornements: use `ORN`, but only for a clearly decorative public-space fixture that does not belong to a more specific category above.
- Parking meters / Horodateurs: use `HOR`.
- Advertising displays / Supports publicitaires: use `PUB`.
- Public toilets / Sanitaire: use `SAN`.
- Ashtrays / Cendriers: use `CEN`.
- Waste and recycling drop-off points / Points d’apport volontaire: use `PAPV`.
- Fire hydrants / Bornes à incendie: use `INC`.
- Speed bumps / Ralentisseurs have no generic fallback: always use one of the subtypes `RAL01` (trapezoidal), `RAL02` (plateau), `RAL03` (Berlin cushion) or `RAL04` (rounded hump). Follow the section SPEED BUMPS AND PEDESTRIAN CROSSINGS: it decides when a speed bump is present and which subtype to choose.

DETECTION AND CLASSIFICATION METHOD

1. Inspect the image itself. Do not infer an object from the filename, location, nearby text, expected inventory or scene context alone.
2. Detect each distinct physical instance separately. A row of five bollards produces five objects and five boxes. A multi-panel barrier may be one object if it is physically continuous; separate disconnected panels get separate boxes.
3. Use a specific Fensch code only when the visible geometry supports its description. Compare shape and topology first: arrangement of parts, roof and wall structure, seat/back/supports, barrier infill, bin opening and body, bollard head/shaft/base, bicycle-support loop, and speed-bump profile. Color, logos, temporary stickers, shadows, contents, vegetation and surface wear are secondary evidence.
4. A different viewpoint can reverse left and right and can foreshorten shapes. Occluded features are unknown, not absent. Do not reject a described model only because a nonessential detail is hidden or has a minor variation.
5. When the category is clear but none of its described specific designs matches, use that category's fallback code. A fallback code means “this category, another design”; it does not mean the image is too blurry to classify.
6. If visibility is insufficient even to establish the category, omit the object. Do not turn uncertainty, blur or severe occlusion into a fallback detection.
7. Do not output objects outside the CSV category list. Do not assign one physical object both a specific code and its fallback code. An accessory integrated into a larger item is not a second detection unless it is a visibly distinct collectable object in its own right.

SPEED BUMPS AND PEDESTRIAN CROSSINGS

- A speed bump is a raised section of the carriageway that vehicles drive over. A pedestrian crossing is only paint on the road: parallel white bars across the carriageway. Pedestrian crossings are not a requested category. Never report a speed bump because of zebra stripes alone, however wide or close they are.
- The main evidence of a speed bump is a row of white triangles ("dents de requin", shark teeth) painted side by side across the lane, on the ramp. Shark teeth are enough on their own: when you see them, report the speed bump.
- Other evidence of a raised section: a ramp edge or step line running across the lane, a bounded patch of different surface (red or ochre coating, block paving, another asphalt), a slope visible through shading or through the bending of lane markings, or a road surface that rises to kerb level.
- A zebra crossing can lie on a speed bump (a raised crossing). Report the speed bump only when shark teeth, a ramp or a surface change show that the crossing is raised. Then box the whole raised section (both ramps and the top, including the shark teeth), not only the stripes.
- Do not miss partly visible speed bumps. One visible ramp is enough. The vehicle often drives over the bump, so the part under the car is hidden: box the visible part on the road surface and set occluded or truncated. Both ramps and the top of one bump form one object. Separate rows of shark teeth on different roads of a junction belong to separate bumps unless a continuous raised surface joins them.
- Choose the subtype from the profile and extent:
  - `RAL01` trapezoidal: two straight ramps with a short flat top a few metres long, across the whole lane or road; it often carries a pedestrian crossing.
  - `RAL02` plateau: a long flat raised area, roughly 10 m or more, often covering a whole junction or a street section, with ramps far apart and often a different surface.
  - `RAL03` Berlin cushion: a rectangular cushion covering only part of the lane width, as in the description; wheels can straddle it.
  - `RAL04` rounded hump: a curved profile with no flat top, short (about 4 m) and across the whole lane.
- When a speed bump is clearly present but its subtype is uncertain (for example only one ramp is visible), choose the most likely subtype with confidence `low`, and name the missing discriminator in `visible_evidence`. Omit a speed bump only when its presence itself is uncertain.

BOUNDING BOX RULES

- Use one tight axis-aligned box around the visible extent of the physical object.
- Include attached structural parts such as the complete shelter roof, bench supports, barrier posts, bollard base or bicycle loop.
- Exclude cast shadows, reflections, labels floating outside the object and unrelated neighbouring objects.
- For an object cut by the image boundary, clip the box to the image; never extrapolate its hidden extent.
- Coordinates use normalized `xywh` relative to the display-oriented image after EXIF rotation: `x` and `y` are the top-left corner, and `width` and `height` are the box size. Every value is between 0 and 1; width and height are greater than 0; `x + width <= 1`; `y + height <= 1`. Round to at most six decimal places.
- Example: pixel edges `(100, 160)` to `(400, 640)` in a `1000 × 800` displayed image become `{ "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.6 }`.

CONFIDENCE

- `high`: category and code are supported by clear, distinctive visible geometry.
- `medium`: the result is likely but a useful discriminator is small, partly occluded or affected by viewpoint.
- `low`: use only for a category fallback or broad direct category that is still more likely than alternatives, or for a speed-bump subtype when the speed bump itself is certain. Never use low confidence to force a specific described design.

OUTPUT

Return valid JSON only, without Markdown fences, prose before the JSON, comments or trailing commas. Preserve the input image order. Use the displayed image dimensions if reliably available; otherwise set `width` and `height` to null. Use the supplied filename as `image_file`, or a stable `image_1`, `image_2`, etc. when no filename is available.

Use exactly this structure:

```json
{
  "schema_version": "1.0.0",
  "coordinate_system": "normalized_xywh",
  "images": [
    {
      "image_file": "query-image.jpg",
      "width": 1920,
      "height": 1080,
      "objects": [
        {
          "object_id": "object_1",
          "code": "POT_CHO_04",
          "category_fr": "Potelets",
          "category_en": "Bollards",
          "classification": "category_fallback",
          "bbox": {
            "x": 0.125,
            "y": 0.25,
            "width": 0.1,
            "height": 0.6
          },
          "confidence": "medium",
          "occluded": false,
          "truncated": false,
          "visible_evidence": [
            "slender ground-fixed post",
            "head geometry differs from all described POT_FEN models"
          ]
        }
      ]
    }
  ]
}
```

`classification` must be one of:

- `described_model` for a code present in `fensch_image_descriptions.json`, except `RAL03`, which uses `shape_subtype`;
- `category_fallback` for `ABR_FEN_01`, `BAN_CHO_01`, `BAR_CHO_03`, `COR_CHO_01`, `ECL_SIE_06`, `POT_CHO_04` or `VEL_FEN_06`;
- `direct_category` for `ARB`, `BAC`, `BOU-E`, `CEN`, `GRI`, `HOR`, `INC`, `ORN`, `PAPV`, `PUB` or `SAN`;
- `shape_subtype` for `RAL01`, `RAL02`, `RAL03` or `RAL04`.

For a fallback result, `visible_evidence` must state both the visible category evidence and at least one structural reason it does not match the closest described models. For a described model, list two independent visible traits when available. Keep evidence concise and observable; never include hidden chain-of-thought, unsupported manufacturer claims or nonvisual assumptions.

If no requested objects are visible, return the image with an empty `objects` array.

REFERENCE FILE CONTENTS

`categories_to_collect_fr_en.csv`: [attach the file or paste its complete content]

`fensch_image_descriptions.json`: [attach the file or paste its complete content]

QUERY_IMAGE(S): [attach one or more images]

---

Recommended usage: provide original-resolution query images. For small distant fixtures, also provide a crop while retaining the full image for bounding-box coordinates. If a crop is supplied, state whether its coordinates should be returned relative to the crop or mapped back to the full image.
