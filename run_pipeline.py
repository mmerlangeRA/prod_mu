"""Run the Fensch pipeline step by step on a range of frames; every step is resumable and re-running shows where it is.

Usage:
  .venv/bin/python run_pipeline.py --batch first100 --first 100            # steps 1-4, then stops for stage 2
  .venv/bin/python run_pipeline.py --batch first100 --first 100 --apply    # step 6, once decisions.json is filled
  .venv/bin/python run_pipeline.py --batch first100 --status               # what is done, what is next

Steps (details in claude.md, "Process"):
  1. Car mask (car_mask.py), once per video. `prepare` runs automatically; the outline is traced by hand once
     (car_masks/<video>/outline.json), then `render` writes mask.png. Without a mask the pipeline stops here.
  2. Stage 1, categories (detect_vlm.py): local Ollama model, car greyed out, resumable frame by frame;
     writes analysis/<batch>.json.
  3. Localization and merge (localize.py, run by detect_vlm.py): positions, features, exports, viewer.
  4. Stage 2 sheets (typing_sheets.py): contact sheets and a decisions.json template in analysis/raw/<batch>/typing/.
  5. Stage 2 decisions: done visually by Claude (code, confidence, state, evidence per feature).
  6. Apply (apply_typing.py): analysis/<batch>-typed.json, re-localized, viewer rebuilt.
Then review in the viewer (review_server.py), which saves analysis/<batch>-typed-reviewed.json.
"""
import argparse
import json
import subprocess
import sys

import fensch

PYTHON = sys.executable


def run(*command):
    print("$ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], cwd=fensch.ROOT, check=True)


def frames(args):
    images = fensch.query_images()
    return images[args.start:None if args.first is None else args.start + args.first]


def status(args):
    batch, raw = args.batch, fensch.ANALYSIS_DIR / "raw" / args.batch
    selection = frames(args) if args.first is not None or args.start else None
    videos = sorted({fensch.frame_key(p.name)[0] for p in (selection or fensch.query_images())})
    masks = {video: (fensch.CAR_MASK_DIR / video / "mask.png").exists() for video in videos}
    done_frames = len(list(raw.glob("*_equirect_anonymized.json"))) if raw.exists() else 0
    decisions_file = raw / "typing" / "decisions.json"
    decisions = json.loads(decisions_file.read_text(encoding="utf-8")) if decisions_file.exists() else None
    rows = [
        ("1 car mask", all(masks.values()), ", ".join(f"{v}: {'ok' if ok else 'missing'}" for v, ok in masks.items())),
        ("2 stage 1", (fensch.ANALYSIS_DIR / f"{batch}.json").exists(), f"{done_frames} frames saved in {raw}"),
        ("3 localize", (fensch.EXPORT_DIR / f"{batch}.features.csv").exists(), f"exports/{batch}.*"),
        ("4 stage-2 sheets", decisions is not None, str(decisions_file.parent)),
        ("5 stage-2 decisions", bool(decisions) and all(d.get("code") for d in decisions.values()),
         f"{sum(1 for d in decisions.values() if d.get('code'))}/{len(decisions)} decided" if decisions else "-"),
        ("6 apply", (fensch.ANALYSIS_DIR / f"{batch}-typed.json").exists(), f"analysis/{batch}-typed.json"),
    ]
    for name, ok, detail in rows:
        print(f"  [{'x' if ok else ' '}] {name:20s} {detail}")
    return dict((name, ok) for name, ok, _ in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--first", type=int, default=None)
    parser.add_argument("--apply", action="store_true", help="step 6: apply the filled stage-2 decisions")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.status:
        status(args)
        return
    if args.apply:
        typed = fensch.ANALYSIS_DIR / f"{args.batch}-typed.json"
        run(PYTHON, "apply_typing.py", fensch.ANALYSIS_DIR / f"{args.batch}.json", *(["--overwrite"] if typed.exists() else []))
        status(args)
        return

    # Step 1: car mask for every video in the selection.
    videos = sorted({fensch.frame_key(p.name)[0] for p in frames(args)})
    missing = [video for video in videos if not (fensch.CAR_MASK_DIR / video / "mask.png").exists()]
    for video in missing:
        if not (fensch.CAR_MASK_DIR / video / "median.jpg").exists():
            run(PYTHON, "car_mask.py", "prepare", "--video", video)
        if (fensch.CAR_MASK_DIR / video / "outline.json").exists():
            run(PYTHON, "car_mask.py", "render", "--video", video)
    missing = [video for video in videos if not (fensch.CAR_MASK_DIR / video / "mask.png").exists()]
    if missing:
        raise SystemExit("Step 1: trace the car outline into car_masks/<video>/outline.json for " + ", ".join(missing) +
                         " (see car_mask.py), then run this command again.")

    # Steps 2-3: stage 1, then localize and viewer (detect_vlm.py runs both at the end; resumable).
    run(PYTHON, "-u", "detect_vlm.py", "--batch", args.batch, "--start", args.start,
        *(["--first", args.first] if args.first is not None else []))
    # Step 4: stage-2 contact sheets (an existing decisions.json is kept).
    run(PYTHON, "typing_sheets.py", fensch.ANALYSIS_DIR / f"{args.batch}.json")
    print(f"\nStep 5: fill analysis/raw/{args.batch}/typing/decisions.json from the sheets (claude.md, stage 2), then run:\n"
          f"  .venv/bin/python run_pipeline.py --batch {args.batch} --apply")
    status(args)


if __name__ == "__main__":
    main()
