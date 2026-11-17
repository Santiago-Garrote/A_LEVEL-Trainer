#!/usr/bin/env python3
"""Serve the app at / and the built packs (data/packs) at /packs/.

  python pipeline/serve.py [port] [--bind 0.0.0.0]

The packs stay in data/ (never inside app/), so hosting app/ elsewhere doesn't publish them.
"""
import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP, PACKS = ROOT / "app", ROOT / "data" / "packs"


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        if path.startswith("/packs/"):
            rel = super().translate_path("/" + path[len("/packs/"):])  # resolved under APP
            return str(PACKS / Path(rel).relative_to(APP))
        return super().translate_path(path)

    def end_headers(self):
        # the catalog must always be fresh so new builds show up as updates
        if self.path.startswith("/packs/index.json"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()


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
