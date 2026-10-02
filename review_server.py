"""Serve the project folder for detection-viewer.html and save reviewed batches from its editor.

Usage:
  python3 review_server.py [--port 8765]
  then open http://127.0.0.1:8765/detection-viewer.html

POST /api/save {"name": "<batch>-reviewed", "document": {...}} writes analysis/<batch>-reviewed.json, then runs
localize.py on it and rebuilds detection-viewer.html. Only names ending in "-reviewed" are accepted, so original
batches are never overwritten; a reviewed batch is a working copy and each save replaces it.
Listens on 127.0.0.1 only and rejects cross-origin requests.
"""
import argparse
import json
import re
import subprocess
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import fensch

MAX_BODY = 50 * 1024 * 1024
NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]+-reviewed")


def check_document(document, catalogue):
    """Return a list of problems that would make localize.py or the viewer fail."""
    problems = []
    images = document.get("images") if isinstance(document, dict) else None
    if not isinstance(images, list) or not images:
        return ["images must be a non-empty list"]
    for i, image in enumerate(images):
        if not (fensch.QUERY_DIR / str(image.get("image_file", ""))).is_file():
            problems.append(f"images[{i}]: unknown image_file {image.get('image_file')!r}")
        ids = set()
        for j, obj in enumerate(image.get("objects", [])):
            where = f"images[{i}].objects[{j}]"
            if obj.get("code") not in catalogue:
                problems.append(f"{where}: unknown code {obj.get('code')!r}")
            if obj.get("object_id") in ids:
                problems.append(f"{where}: duplicate object_id {obj.get('object_id')!r}")
            ids.add(obj.get("object_id"))
            box = obj.get("bbox") or {}
            values = [box.get(k) for k in ("x", "y", "width", "height")]
            if not all(isinstance(v, (int, float)) for v in values):
                problems.append(f"{where}: bbox must have numeric x, y, width, height")
            elif values[0] < 0 or values[1] < 0 or values[2] <= 0 or values[3] <= 0 \
                    or values[0] + values[2] > 1 + 1e-9 or values[1] + values[3] > 1 + 1e-9:
                problems.append(f"{where}: bbox outside the image")
            if obj.get("confidence") not in ("high", "medium", "low"):
                problems.append(f"{where}: confidence must be high, medium or low")
            if obj.get("state") is not None and obj["state"] not in fensch.STATES:
                problems.append(f"{where}: state must be one of {', '.join(fensch.STATES)}")
            manual = obj.get("manual_position")
            if manual is not None and not (isinstance(manual, dict) and isinstance(manual.get("edited"), bool)
                                           and all(isinstance(manual.get(k), (int, float)) for k in ("latitude", "longitude"))
                                           and -90 <= manual["latitude"] <= 90 and -180 <= manual["longitude"] <= 180):
                problems.append(f"{where}: manual_position must have numeric latitude, longitude and a boolean edited")
    return problems


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/save":
            return self.reply(404, {"ok": False, "error": "unknown endpoint"})
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://{host}", f"http://localhost:{self.server.server_port}",
                                     f"http://127.0.0.1:{self.server.server_port}"):
            return self.reply(403, {"ok": False, "error": "cross-origin request refused"})
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.reply(415, {"ok": False, "error": "expected application/json"})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            return self.reply(413, {"ok": False, "error": "empty or too large request"})
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            return self.reply(400, {"ok": False, "error": f"invalid JSON: {error}"})
        name, document = payload.get("name", ""), payload.get("document")
        if not NAME_PATTERN.fullmatch(name):
            return self.reply(400, {"ok": False, "error": "name must end with -reviewed and use only letters, digits, '.', '_' or '-'"})
        problems = check_document(document, fensch.load_catalogue())
        if problems:
            return self.reply(422, {"ok": False, "error": "invalid batch", "problems": problems[:50]})

        target = fensch.ANALYSIS_DIR / f"{name}.json"
        document["batch"] = name
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        steps = []
        for command in ([sys.executable, "localize.py", str(target)], ["node", "build-detection-viewer.mjs"]):
            result = subprocess.run(command, cwd=fensch.ROOT, capture_output=True, text=True)
            steps.append({"command": " ".join(command[1:] if command[0] == sys.executable else command),
                          "returncode": result.returncode, "output": (result.stdout + result.stderr)[-4000:]})
            if result.returncode:
                return self.reply(500, {"ok": False, "error": f"{steps[-1]['command']} failed", "saved": f"analysis/{target.name}", "steps": steps})
        self.reply(200, {"ok": True, "saved": f"analysis/{target.name}", "steps": steps})

    def reply(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        # The viewer is rebuilt after each save; never let the browser serve a stale copy.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(fensch.ROOT)))
    print(f"Serving {fensch.ROOT} at http://127.0.0.1:{args.port}/detection-viewer.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
