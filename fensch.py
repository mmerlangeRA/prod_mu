"""Shared catalogue, GPS-frame and geometry helpers for the Fensch pipeline."""
import csv
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUERY_DIR = ROOT / "queries"
ANALYSIS_DIR = ROOT / "analysis"
EXPORT_DIR = ROOT / "exports"

CLAVIER_FILE = ROOT / "Clavier Equipements CA_Val_de_Fensch.json"
CATEGORIES_FILE = ROOT / "categories_to_collect_fr_en.csv"
DESCRIPTIONS_FILE = ROOT / "fensch_image_descriptions.json"
PROMPT_FILE = ROOT / "fensch_bbox_prompt.md"
GPS_FILE = ROOT / "ncp_gps_frames.json"

FRAME_PATTERN = re.compile(r"^(?P<video>.+)_f_(?P<frame>\d+)_")

# Code prefix -> French category name, exactly as spelled in categories_to_collect_fr_en.csv.
CATEGORY_BY_PREFIX = {
    "ABR": "Abris bus",
    "BAN": "Bancs",
    "BAR": "Barrières",
    "COR": "Corbeille",
    "ECL": "Points d’éclairage",
    "POT": "Potelets",
    "VEL": "Supports vélos",
    "RAL": "Ralentisseurs",
    "ARB": "Arbres",
    "BAC": "Bacs à fleurs",
    "BOU-E": "Bouches d’égout",
    "CEN": "Cendriers",
    "GRI": "Grilles, avaloirs",
    "HOR": "Horodateurs",
    "INC": "Bornes à incendie",
    "ORN": "Ornements",
    "PAPV": "Points d’apport volontaire",
    "PUB": "Supports publicitaires",
    "SAN": "Sanitaire",
}

# Objects lying on the ground: localize their box centre instead of the bottom edge.
FLAT_PREFIXES = {"BOU-E", "GRI", "RAL"}

# Condition set during stage-2 typing, best to worst: good is the norm, damaged is significantly deteriorated, bad is
# not or barely functional. Mapped to the Clavier's mandatory "Etat" part (Bon / Moyen / Mauvais).
STATES = ("good", "damaged", "bad")
ETAT_BY_STATE = {"good": "Bon", "damaged": "Moyen", "bad": "Mauvais"}


def code_prefix(code):
    return code if code in CATEGORY_BY_PREFIX else code.split("_")[0].rstrip("0123456789")


def load_catalogue():
    """Return {code: {label, category_fr, category_en, classification}} for every Clavier code."""
    clavier = json.loads(CLAVIER_FILE.read_text(encoding="utf-8"))
    type_part = next(p for r in clavier["rubrics"] for p in r["parts"] if p["name"] == "Type")
    with CATEGORIES_FILE.open(encoding="utf-8-sig", newline="") as handle:
        english = {row["name_fr"]: row["name_en"] for row in csv.DictReader(handle)}
    described = set(json.loads(DESCRIPTIONS_FILE.read_text(encoding="utf-8")))
    catalogue = {}
    for lexicon in type_part["lexicons"]:
        code, label = lexicon["code"], lexicon["value"].strip()
        category_fr = CATEGORY_BY_PREFIX[code_prefix(code)]
        if code.startswith("RAL"):
            classification = "shape_subtype"
        elif "classifier" in label or "déterminer" in label:
            classification = "category_fallback"
        elif code in described:
            classification = "described_model"
        else:
            classification = "direct_category"
        catalogue[code] = {
            "label": label,
            "category_fr": category_fr,
            "category_en": english[category_fr],
            "classification": classification,
        }
    return catalogue


def orientation_to_heading(orientation):
    """ncp_gps_frames.json `orientation` is the GPS bearing as a math angle (degrees counter-clockwise from east).

    The compass heading (clockwise from north) is 90 - orientation: it matches the bearing between consecutive GPS
    positions within 3.6° (median) on the gs010136 track, and the panorama centre column faces that direction.
    """
    return (90.0 - orientation) % 360.0


def load_frames():
    """Return {(video_name, frame): {latitude, longitude, heading_deg, orientation, timestamp_ms}} from ncp_gps_frames.json."""
    frames = {}
    for entry in json.loads(GPS_FILE.read_text(encoding="utf-8")):
        for image in entry["images"]:
            frames[(image["video_name"], int(image["frame"]))] = {
                "latitude": entry["latitude"],
                "longitude": entry["longitude"],
                "heading_deg": round(orientation_to_heading(entry["orientation"]), 4),
                "orientation": entry["orientation"],
                "timestamp_ms": entry["timestamp_ms"],
            }
    return frames


def frame_key(image_file):
    match = FRAME_PATTERN.match(Path(image_file).name)
    if not match:
        raise ValueError(f"Cannot read video and frame from {image_file!r}")
    return match["video"], int(match["frame"])


def query_images():
    """Query panoramas sorted by (video, frame)."""
    return sorted((p for p in QUERY_DIR.iterdir() if FRAME_PATTERN.match(p.name)), key=lambda p: frame_key(p.name))


def destination_point(latitude, longitude, bearing_deg, distance_m):
    radius = 6371000.0
    bearing, angular = math.radians(bearing_deg), distance_m / radius
    lat1, lon1 = math.radians(latitude), math.radians(longitude)
    lat2 = math.asin(math.sin(lat1) * math.cos(angular) + math.cos(lat1) * math.sin(angular) * math.cos(bearing))
    lon2 = lon1 + math.atan2(math.sin(bearing) * math.sin(angular) * math.cos(lat1), math.cos(angular) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180


def ground_distance_m(lat1, lon1, lat2, lon2):
    """Equirectangular approximation; accurate to centimetres over the distances used here."""
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = math.radians(lon2 - lon1) * math.cos(mean_lat)
    dy = math.radians(lat2 - lat1)
    return 6371000.0 * math.hypot(dx, dy)
