#!/usr/bin/env python3
"""Serve the app at / and the built packs (data/packs) at /packs/.

  python pipeline/serve.py [port] [--bind 0.0.0.0]

The packs stay in data/ (never inside app/), so hosting app/ elsewhere doesn't publish them.

It also lets the app convert PapaCambridge links into packs on this machine:
  GET  /api/status   -> {"convert": true}       (absent on static hosting, so the app hides the feature)
  POST /api/convert  {"links": [...]}            runs `papers.py links ...` in the background
  GET  /api/convert  -> {running, exit, log}     progress of the current/last run
"""
import argparse
import functools
import json
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
APP, PACKS = ROOT / "app", ROOT / "data" / "packs"
PIPELINE = Path(__file__).resolve().parent / "papers.py"
PAPA_HOSTS = {"pastpapers.papacambridge.com", "papacambridge.com", "www.papacambridge.com"}

job = {"proc": None, "links": [], "log": [], "exit": None}
lock = threading.Lock()


def start(links):
    proc = subprocess.Popen([sys.executable, "-u", str(PIPELINE), "links", *links], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    job.update(proc=proc, links=links, log=[], exit=None)

    def pump():
        for line in proc.stdout:
            with lock:
                job["log"] = (job["log"] + [line.rstrip()])[-400:]
        job["exit"] = proc.wait()

    threading.Thread(target=pump, daemon=True).start()


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        if path.startswith("/packs/"):
            rel = super().translate_path("/" + path[len("/packs/"):])  # resolved under APP
            return str(PACKS / Path(rel).relative_to(APP))
        return super().translate_path(path)

    def end_headers(self):
        # the catalog must always be fresh so new builds show up as updates
        if self.path.startswith(("/packs/index.json", "/api/")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            return self.send_json({"convert": True})
        if self.path == "/api/convert":
            with lock:
                running = job["proc"] is not None and job["exit"] is None
                return self.send_json({"running": running, "exit": job["exit"], "links": job["links"], "log": job["log"]})
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/convert":
            return self.send_json({"error": "not found"}, 404)
        # only the app itself may start jobs: other sites can't send JSON here without a CORS preflight
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return self.send_json({"error": "forbidden"}, 403)
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.send_json({"error": "expected JSON"}, 415)
        try:
            links = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))["links"]
            links = [str(l).strip() for l in links if str(l).strip()]
        except (ValueError, KeyError, TypeError):
            return self.send_json({"error": "expected {\"links\": [...]}"}, 400)
        bad = [l for l in links if urlparse(l).scheme != "https" or urlparse(l).hostname not in PAPA_HOSTS]
        if not links or bad:
            return self.send_json({"error": f"only PapaCambridge https links are supported: {', '.join(bad) or 'none given'}"}, 400)
        with lock:
            if job["proc"] is not None and job["exit"] is None:
                return self.send_json({"error": "a conversion is already running"}, 409)
            start(links)
        return self.send_json({"started": True}, 202)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", type=int, default=8000)
    ap.add_argument("--bind", default="127.0.0.1")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.bind, args.port), functools.partial(Handler, directory=str(APP)))
    print(f"App on http://{args.bind}:{args.port}/  (packs from {PACKS})")
    server.serve_forever()


if __name__ == "__main__":
    main()
