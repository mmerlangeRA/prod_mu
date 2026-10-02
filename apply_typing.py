"""Stage 2, step 2: apply the typing decisions to a batch, as a new batch.

Usage:
  .venv/bin/python apply_typing.py analysis/<batch>.json [--decisions FILE] [--output NAME] [--overwrite]

Reads analysis/raw/<batch>/typing/decisions.json (written by typing_sheets.py and filled in), where each feature has:
  "code": a catalogue code (it may be in another category, e.g. a "bollard" that is really a bicycle rack),
          "REJECT" (not equipment: wheelie bin, sign, reflection...), or null (undecided: left unchanged);
  "confidence": "high" | "medium" | "low";
  "state": "good" | "damaged" | "bad" (null for REJECT): good is the norm, damaged is significantly deteriorated
           (leaning, dented, broken part, heavy rust...), bad is not or barely functional (knocked down, broken off...);
  "evidence": short visible reasons, including the reason for a damaged or bad state.
Every detection of a decided feature gets the code, its category and classification, code_status "typed", the state
and the evidence; rejected features lose all their detections (kept under the image's typing.rejected). The result is written
to analysis/<batch>-typed.json (the source batch is never modified), then localize.py and the viewer build are run.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import fensch


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("analysis_file")
    parser.add_argument("--decisions")
    parser.add_argument("--output", help="batch name of the result (default: <batch>-typed)")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing typed batch")
    args = parser.parse_args()

    source = Path(args.analysis_file)
    data = json.loads(source.read_text(encoding="utf-8"))
    batch = data.get("batch") or source.stem
    decisions_file = Path(args.decisions) if args.decisions else fensch.ANALYSIS_DIR / "raw" / batch / "typing" / "decisions.json"
    decisions = json.loads(decisions_file.read_text(encoding="utf-8"))
    catalogue = fensch.load_catalogue()
    name = args.output or f"{batch}-typed"
    target = fensch.ANALYSIS_DIR / f"{name}.json"
    if target.exists() and not args.overwrite:
        raise SystemExit(f"{target} exists; use --overwrite to replace it.")

    problems = [f"{fid}: unknown code {d['code']!r}" for fid, d in decisions.items()
                if d.get("code") not in (None, "REJECT") and d["code"] not in catalogue]
    problems += [f"{fid}: state must be one of {', '.join(fensch.STATES)}" for fid, d in decisions.items()
                 if d.get("code") not in (None, "REJECT") and d.get("state") is not None and d["state"] not in fensch.STATES]
    problems += [f"{fid}: unknown feature" for fid in decisions if fid not in {f["feature_id"] for f in data.get("features", [])}]
    if problems:
        raise SystemExit("Invalid decisions:\n" + "\n".join(problems))

    members = {f["feature_id"]: {(d["image_file"], d["object_id"]) for d in f["detections"]} for f in data["features"]}
    decision_of = {}
    for feature_id, decision in decisions.items():
        if decision.get("code") is not None:
            for key in members[feature_id]:
                decision_of[key] = (feature_id, decision)

    typed = rejected = 0
    for image in data["images"]:
        kept = []
        for obj in image["objects"]:
            entry = decision_of.get((image["image_file"], obj["object_id"]))
            if entry is None:
                kept.append(obj)
                continue
            feature_id, decision = entry
            if decision["code"] == "REJECT":
                image.setdefault("typing", {}).setdefault("rejected", []).append(
                    {"object_id": obj["object_id"], "code": obj["code"], "bbox": obj["bbox"], "feature_id": feature_id,
                     "evidence": decision.get("evidence", [])})
                rejected += 1
                continue
            entry_catalogue = catalogue[decision["code"]]
            obj.update({"code": decision["code"], "category_fr": entry_catalogue["category_fr"],
                        "category_en": entry_catalogue["category_en"], "classification": entry_catalogue["classification"],
                        "code_status": "typed", "typed_from_feature": feature_id})
            if decision.get("confidence") in ("high", "medium", "low"):
                obj["confidence"] = decision["confidence"]
            if decision.get("state") in fensch.STATES:
                obj["state"] = decision["state"]
            if decision.get("evidence"):
                obj["visible_evidence"] = list(decision["evidence"])
            typed += 1
            kept.append(obj)
        image["objects"] = kept

    data["batch"] = name
    data["typing"] = {"source_batch": batch, "decisions_file": str(decisions_file.resolve().relative_to(fensch.ROOT) if decisions_file.resolve().is_relative_to(fensch.ROOT) else decisions_file),
                      "decided_features": sum(d.get("code") is not None for d in decisions.values()),
                      "undecided_features": sum(d.get("code") is None for d in decisions.values()),
                      "states": {state: sum(d.get("code") not in (None, "REJECT") and d.get("state") == state
                                            for d in decisions.values()) for state in fensch.STATES}}
    data.pop("features", None)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {target}: {typed} detections typed, {rejected} rejected, "
          f"{data['typing']['undecided_features']} features left undecided")
    subprocess.run([sys.executable, "localize.py", str(target)], cwd=fensch.ROOT, check=True)
    subprocess.run(["node", "build-detection-viewer.mjs"], cwd=fensch.ROOT, check=False)


if __name__ == "__main__":
    main()
