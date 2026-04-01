#!/usr/bin/env python3
"""
serve_runs.py — Serve the runs/ directory over HTTP with CORS headers.

This allows the local visualizer (localhost:5173) to fetch submission.log
files from the runs directory via the ?open= URL parameter.

Usage:
    python scripts/serve_runs.py [--port 8080]

Runs on port 8080 by default.
"""

import argparse
import os
import sys
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path


class CORSHandler(SimpleHTTPRequestHandler):
    """HTTP handler with CORS headers for cross-origin visualizer access."""

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        # Cleaner log format
        sys.stdout.write(f"[serve] {args[0]}\n")
        sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description="Serve backtest runs with CORS")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    runs_dir = project_root / "runs"
    runs_dir.mkdir(exist_ok=True)

    os.chdir(runs_dir)
    handler = partial(CORSHandler, directory=str(runs_dir))
    server = HTTPServer(("127.0.0.1", args.port), handler)

    print(f"Serving runs/ at http://localhost:{args.port}")
    print(f"Directory: {runs_dir}")
    print(f"Visualizer URL format: http://localhost:5173/?open=http://localhost:{args.port}/<run_id>/submission.log")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
