"""Estimate ground positions of detections, merge them into features and export the results.

Usage:
  .venv/bin/python localize.py analysis/NAME.json [--camera-height 2.0] [--max-range 15] [--merge-radius 3]

Camera positions and headings come only from ncp_gps_frames.json. Each detection is projected on a flat ground plane
from a level camera; detections of the same category from different frames that fall within --merge-radius are merged
into one feature. The analysis file is updated in place (camera, per-object `ground`, `manual_position`, `localization`,
`features`), and exports/NAME.features.geojson, exports/NAME.features.csv and exports/NAME.detections.geojson are written.
Re-running recomputes everything from the detections.

Positions: `ground` is the automatic position computed from the box. `manual_position` is the position shown on the map
and exported: a copy of the automatic one (`edited: false`, refreshed on every run) until a reviewer moves it in the
viewer (`edited: true`, then kept as is). A feature's position is the mean of its moved detections when it has any,
otherwise the weighted mean of its detections; `auto_latitude`/`auto_longitude` keep the purely automatic estimate.
"""
import argparse
import csv
import json
import math
from pathlib import Path

import fensch

CONFIDENCE_WEIGHT = {"high": 3.0, "medium": 2.0, "low": 1.0}


def anchor_point(obj):
    """Normalized (u, v) of the ground-contact point: bottom centre, or box centre for objects lying on the ground."""
    box = obj["bbox"]
    u = box["x"] + box["width"] / 2
    if fensch.code_prefix(obj["code"]) in fensch.FLAT_PREFIXES:
        return u, box["y"] + box["height"] / 2
    return u, box["y"] + box["height"]


def locate(obj, camera, args, mask=None):
    u, v = anchor_point(obj)
    bearing = (camera["heading_deg"] + (u - 0.5) * 360.0) % 360.0
    elevation = (0.5 - v) * math.pi
    ground = {"anchor_u": round(u, 6), "anchor_v": round(v, 6), "bearing_deg": round(bearing, 2)}
    if elevation >= -1e-6:
        return {**ground, "status": "above_horizon"}
    distance = args.camera_height / math.tan(-elevation)
    ground["distance_m"] = round(distance, 2)
    if distance > args.max_range:
        return {**ground, "status": "beyond_range"}
    if distance < args.min_range:
        return {**ground, "status": "too_close"}
    latitude, longitude = fensch.destination_point(camera["latitude"], camera["longitude"], bearing, distance)
    return {
        **ground,
        "status": "ok",
        # A hidden or cut base makes the box bottom, and therefore the distance, unreliable; so does a contact point on
        # the car mask (car_mask.py), where the car hides the base.
        "reliable": not (obj["occluded"] or obj["truncated"] or (mask is not None and fensch.on_car(mask, u, v))),
        "latitude": round(latitude, 8),
        "longitude": round(longitude, 8),
    }


def merge(detections, radius, catalogue):
    """Greedy merge, nearest sightings first; one feature never takes two detections from the same image."""
    clusters = []
    for det in sorted(detections, key=lambda d: d["distance_m"]):
        best, best_distance = None, None
        for cluster in clusters:
            if cluster["category_fr"] != det["category_fr"] or det["image_file"] in cluster["images"]:
                continue
            gap = fensch.ground_distance_m(cluster["latitude"], cluster["longitude"], *det["position"])
            if gap <= radius and (best is None or gap < best_distance):
                best, best_distance = cluster, gap
        if best is None:
            best = {"category_fr": det["category_fr"], "members": [], "images": set()}
            clusters.append(best)
        best["members"].append(det)
        best["images"].add(det["image_file"])
        best["latitude"], best["longitude"] = cluster_position(best["members"], lambda m: m["position"])

    features = []
    for number, cluster in enumerate(sorted(clusters, key=lambda c: (c["category_fr"], c["latitude"], c["longitude"])), start=1):
        votes = {}
        for member in cluster["members"]:
            votes[member["code"]] = votes.get(member["code"], 0.0) + member_weight(member) / max(member["distance_m"], 1.0)
        code = max(votes, key=votes.get)
        chosen = [m for m in cluster["members"] if m["code"] == code]
        entry = catalogue[code]
        state = feature_state(chosen)
        automatic = [m for m in cluster["members"] if m["ground"].get("status") == "ok"]
        auto_position = cluster_position(automatic, lambda m: (m["ground"]["latitude"], m["ground"]["longitude"]), manual=False) \
            if automatic else (None, None)
        features.append({
            "feature_id": f"F{number:04d}",
            "code": code,
            "category_fr": entry["category_fr"],
            "category_en": entry["category_en"],
            "classification": entry["classification"],
            "latitude": round(cluster["latitude"], 8),
            "longitude": round(cluster["longitude"], 8),
            "position_edited": any(m["edited"] for m in cluster["members"]),
            "auto_latitude": auto_position[0] and round(auto_position[0], 8),
            "auto_longitude": auto_position[1] and round(auto_position[1], 8),
            "confidence": max((m["confidence"] for m in chosen), key=CONFIDENCE_WEIGHT.get),
            "code_agreement": round(votes[code] / sum(votes.values()), 3),
            "state": state,
            "etat": fensch.ETAT_BY_STATE.get(state),
            "n_detections": len(cluster["members"]),
            "min_distance_m": min(m["distance_m"] for m in cluster["members"]),
            "detections": [{"image_file": m["image_file"], "object_id": m["object_id"], "code": m["code"],
                            "distance_m": m["distance_m"]} for m in cluster["members"]],
        })
    return features


def cluster_position(members, position, manual=True):
    """Mean of the moved members when there are any (manual positions win), else weighted by confidence / distance²."""
    edited = [m for m in members if manual and m["edited"]]
    if edited:
        return (sum(position(m)[0] for m in edited) / len(edited), sum(position(m)[1] for m in edited) / len(edited))
    weights = [member_weight(m) / max(m["distance_m"], 1.0) ** 2 for m in members]
    return (sum(w * position(m)[0] for w, m in zip(weights, members)) / sum(weights),
            sum(w * position(m)[1] for w, m in zip(weights, members)) / sum(weights))


def update_manual_position(obj):
    """Keep a moved manual position; otherwise copy the automatic one (or drop it when there is none)."""
    manual = obj.get("manual_position")
    if manual and manual.get("edited"):
        return
    ground = obj["ground"]
    if ground.get("status") == "ok":
        obj["manual_position"] = {"latitude": ground["latitude"], "longitude": ground["longitude"], "edited": False}
    else:
        obj.pop("manual_position", None)


def feature_state(members):
    """Weighted vote over the members' stage-2 states; a tie goes to the worse state. None when no member has one."""
    votes = {}
    for member in members:
        if member.get("state") in fensch.STATES:
            votes[member["state"]] = votes.get(member["state"], 0.0) + member_weight(member)
    if not votes:
        return None
    return max(votes, key=lambda state: (votes[state], fensch.STATES.index(state)))


def member_weight(det):
    return CONFIDENCE_WEIGHT[det["confidence"]] * (1.0 if det["edited"] or det["ground"].get("reliable") else 0.5)


def write_exports(batch_name, data, features):
    fensch.EXPORT_DIR.mkdir(exist_ok=True)
    stem = fensch.EXPORT_DIR / batch_name
    feature_collection = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [f["longitude"], f["latitude"]]},
            "properties": {k: v for k, v in f.items() if k not in ("latitude", "longitude")},
        } for f in features],
    }
    Path(f"{stem}.features.geojson").write_text(json.dumps(feature_collection, ensure_ascii=False, indent=1), encoding="utf-8")

    with open(f"{stem}.features.csv", "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["feature_id", "code", "category_fr", "category_en", "classification", "latitude", "longitude",
                         "confidence", "code_agreement", "state", "etat", "position_edited", "auto_latitude", "auto_longitude",
                         "n_detections", "min_distance_m", "frames"])
        for f in features:
            frames = sorted({fensch.frame_key(d["image_file"])[1] for d in f["detections"]})
            writer.writerow([f["feature_id"], f["code"], f["category_fr"], f["category_en"], f["classification"],
                             f["latitude"], f["longitude"], f["confidence"], f["code_agreement"], f["state"], f["etat"], f["position_edited"],
                             f["auto_latitude"], f["auto_longitude"], f["n_detections"],
                             f["min_distance_m"], " ".join(str(n) for n in frames)])

    detection_features = []
    for image in data["images"]:
        for obj in image["objects"]:
            ground, manual = obj.get("ground", {}), obj.get("manual_position")
            if not manual:
                continue
            automatic = ground.get("status") == "ok"
            detection_features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [manual["longitude"], manual["latitude"]]},
                "properties": {"image_file": image["image_file"], "object_id": obj["object_id"], "code": obj["code"],
                               "confidence": obj["confidence"], "state": obj.get("state"),
                               "position_edited": bool(manual.get("edited")),
                               "auto_latitude": ground["latitude"] if automatic else None,
                               "auto_longitude": ground["longitude"] if automatic else None,
                               "distance_m": ground.get("distance_m"),
                               "bearing_deg": ground.get("bearing_deg"), "reliable": ground.get("reliable"),
                               "feature_id": obj.get("feature_id")},
            })
    Path(f"{stem}.detections.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": detection_features}, ensure_ascii=False, indent=1), encoding="utf-8")
    return [f"{stem}.features.geojson", f"{stem}.features.csv", f"{stem}.detections.geojson"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("analysis_file")
    parser.add_argument("--camera-height", type=float, default=2.0, help="metres above the ground plane")
    parser.add_argument("--max-range", type=float, default=15.0, help="drop detections farther than this (metres)")
    parser.add_argument("--min-range", type=float, default=1.0, help="drop detections closer than this (metres): the vehicle itself")
    parser.add_argument("--merge-radius", type=float, default=3.0, help="metres within which same-category detections merge")
    args = parser.parse_args()

    path = Path(args.analysis_file)
    data = json.loads(path.read_text(encoding="utf-8"))
    frames, catalogue = fensch.load_frames(), fensch.load_catalogue()
    located, counts = [], {}
    for image in data["images"]:
        camera = frames.get(fensch.frame_key(image["image_file"]))
        image["camera"] = camera and {"video_name": fensch.frame_key(image["image_file"])[0],
                                      "frame": fensch.frame_key(image["image_file"])[1], **camera}
        for obj in image["objects"]:
            obj.pop("feature_id", None)
            obj["ground"] = locate(obj, camera, args, fensch.car_mask_for(image["image_file"])) if camera else {"status": "no_gps"}
            counts[obj["ground"]["status"]] = counts.get(obj["ground"]["status"], 0) + 1
            update_manual_position(obj)
            manual = obj.get("manual_position")
            if manual:
                position = (manual["latitude"], manual["longitude"])
                # A moved position can exist without an automatic one (e.g. a box beyond range placed by hand).
                distance = obj["ground"].get("distance_m") if obj["ground"].get("status") == "ok" else \
                    (fensch.ground_distance_m(camera["latitude"], camera["longitude"], *position) if camera else 1.0)
                located.append({**obj, "image_file": image["image_file"], "position": position, "distance_m": distance,
                                "edited": bool(manual.get("edited"))})

    features = merge(located, args.merge_radius, catalogue)
    feature_of = {(d["image_file"], d["object_id"]): f["feature_id"] for f in features for d in f["detections"]}
    for image in data["images"]:
        for obj in image["objects"]:
            if (image["image_file"], obj["object_id"]) in feature_of:
                obj["feature_id"] = feature_of[(image["image_file"], obj["object_id"])]
    data["localization"] = {
        "gps_source": fensch.GPS_FILE.name,
        "camera_height_m": args.camera_height,
        "max_range_m": args.max_range,
        "min_range_m": args.min_range,
        "merge_radius_m": args.merge_radius,
        "heading": "compass heading = 90 - ncp_gps_frames.json orientation (GPS bearing as a math angle), "
                   "taken as the heading of the panorama centre column",
    }
    data["features"] = features
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    batch_name = data.get("batch") or path.stem
    outputs = write_exports(batch_name, data, features)
    print(f"Detections by status: {dict(sorted(counts.items()))}")
    print(f"{len(features)} features from {len(located)} located detections; updated {path}")
    for output in outputs:
        print(f"Wrote {output}")


if __name__ == "__main__":
    main()
