#!/usr/bin/env python3
"""
watch_runs.py — Watch runs/latest for changes and notify the visualizer via WebSocket.

When a new backtest completes (submission.log updated), broadcasts a reload
signal so the visualizer can auto-refresh without manual intervention.

Usage:
    python scripts/watch_runs.py [--port 8081]

Requires: pip install watchdog websockets
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

try:
    import websockets
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    print("ERROR: Required packages missing. Install with:", file=sys.stderr)
    print("  pip install watchdog websockets", file=sys.stderr)
    sys.exit(1)


connected_clients: set = set()
event_queue: asyncio.Queue = asyncio.Queue()


class RunsHandler(FileSystemEventHandler):
    """Watch for submission.log changes in runs/."""

    def __init__(self):
        self.last_event = 0

    def on_modified(self, event):
        if event.is_directory:
            return
        if "submission.log" in event.src_path:
            now = time.time()
            if now - self.last_event < 1.0:  # Debounce 1s
                return
            self.last_event = now
            print(f"[watch] Detected change: {event.src_path}")
            asyncio.get_event_loop().call_soon_threadsafe(
                event_queue.put_nowait,
                {"type": "reload", "file": event.src_path, "timestamp": now},
            )

    def on_created(self, event):
        self.on_modified(event)


async def broadcast():
    """Consume events and broadcast to all connected WebSocket clients."""
    while True:
        event = await event_queue.get()
        if connected_clients:
            msg = json.dumps(event)
            await asyncio.gather(
                *[client.send(msg) for client in connected_clients],
                return_exceptions=True,
            )
            print(f"[watch] Broadcast to {len(connected_clients)} client(s)")


async def handler(websocket):
    """Handle a new WebSocket connection."""
    connected_clients.add(websocket)
    print(f"[watch] Client connected ({len(connected_clients)} total)")
    try:
        async for _ in websocket:
            pass  # We only send, never receive
    finally:
        connected_clients.discard(websocket)
        print(f"[watch] Client disconnected ({len(connected_clients)} total)")


async def main(port: int, runs_dir: Path):
    # Start file watcher
    observer = Observer()
    observer.schedule(RunsHandler(), str(runs_dir), recursive=True)
    observer.start()

    print(f"[watch] Watching: {runs_dir}")
    print(f"[watch] WebSocket: ws://localhost:{port}")
    print(f"[watch] Press Ctrl+C to stop.")

    # Start WebSocket server + broadcast loop
    async with websockets.serve(handler, "127.0.0.1", port):
        await broadcast()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Watch runs/ and notify visualizer")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    runs_dir = project_root / "runs"
    runs_dir.mkdir(exist_ok=True)

    try:
        asyncio.run(main(args.port, runs_dir))
    except KeyboardInterrupt:
        print("\nStopped.")
