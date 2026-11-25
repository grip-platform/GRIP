#!/usr/bin/env python3
"""mesh_insight_consumer.py — always-on consumer of cross-machine learning insights.

Tails ~/.grip-session-mesh/inbound/*.jsonl (where the L1 bus writes frames the
L2 Tailscale relay forwarded from peer machines) and feeds every kind="insight"
frame to mesh_insight_bridge.ingest_insight() — landing a peer's lesson in THIS
machine's learning/insights.jsonl in real time.

Runs as a launchd keepalive agent (com.grip.mesh-insight-consumer), one per
machine — symmetric. Byte-offset tracked so a restart never reprocesses; reads
only complete (newline-terminated) lines so a half-written frame is retried next
tick; fail-soft so a malformed frame or an absent mesh never breaks the loop.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from mesh_insight_bridge import MESH_DIR, ingest_insight  # noqa: E402

INBOUND_DIR = MESH_DIR / "inbound"
OFFSETS_FILE = MESH_DIR / ".insight-consumer-offsets.json"
POLL_SECONDS = float(os.environ.get("MESH_INSIGHT_POLL_SECONDS", "2"))

_running = True


def _stop(*_args):
    global _running
    _running = False


def _load_offsets() -> dict:
    try:
        return json.loads(OFFSETS_FILE.read_text())
    except Exception:
        return {}


def _save_offsets(offsets: dict) -> None:
    try:
        OFFSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = OFFSETS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(offsets))
        os.replace(tmp, OFFSETS_FILE)
    except OSError:
        pass


def _process_file(path: Path, start: int) -> int:
    """Ingest complete new lines from `start`. Returns the new byte offset.

    Binary readline() + tell() gives true byte offsets (text-mode tell() during
    iteration is unreliable). Stops at a partial (non-newline-terminated) line so
    a frame still being written is picked up complete on the next tick.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return start
    if start > size:  # file truncated / rotated -> reread from the top
        start = 0
    if start >= size:
        return start
    new_off = start
    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            while True:
                raw = fh.readline()
                if not raw or not raw.endswith(b"\n"):
                    break  # EOF or partial line — retry next tick
                new_off = fh.tell()
                try:
                    frame = json.loads(raw.decode("utf-8", "replace").strip())
                except Exception:
                    continue
                try:
                    ingest_insight(frame)
                except Exception:
                    pass
    except OSError:
        return start
    return new_off


def _tick(offsets: dict) -> None:
    if not INBOUND_DIR.exists():
        return
    for path in sorted(INBOUND_DIR.glob("*.jsonl")):
        offsets[path.name] = _process_file(path, offsets.get(path.name, 0))


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    offsets = _load_offsets()
    while _running:
        try:
            _tick(offsets)
            _save_offsets(offsets)
        except Exception:
            pass
        time.sleep(POLL_SECONDS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
