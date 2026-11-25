"""The state file and the log: load, atomic save, append.

``state/radar/state.json`` holds, per source, ``last_run`` plus that source's own
slice (seen ids, counters, the mail mode). ``logs/radar.jsonl`` gets one object
per source per tick. Both paths are gitignored (``state/**/*.json``, ``logs/``).

The save writes a sibling ``.tmp`` and renames it over the state file, as
``lib/wa_watcher.py`` does, so a crash mid-write leaves the previous state intact
rather than a half-written file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

VERSION = 1
SEEN_CAP = 500                # seen-id lists keep the last 500, as lib/wa_watcher.py does
PENDING_TTL_S = 24 * 3600     # an alert the transport could not deliver is dropped after a day


def default_state() -> dict:
    return {
        "version": VERSION,
        "seeded_at": None,
        "pending_alerts": [],
        "sources": {},
        "rollup": {"last_day": None, "items": []},
    }


def load(path: Path) -> tuple[dict | None, str | None]:
    """``(state, None)`` when the file is good; ``(None, reason)`` when it is
    absent or unreadable. The caller seeds again on None, silently."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "absent"
    except OSError as exc:
        return None, f"unreadable: {type(exc).__name__}"
    try:
        data = json.loads(text)
    except ValueError:
        return None, "corrupt"
    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        return None, "corrupt"
    data.setdefault("pending_alerts", [])
    data.setdefault("rollup", {"last_day": None, "items": []})
    return data, None


def save(path: Path, state: dict) -> None:
    """Atomic: write ``<name>.tmp`` beside the file, then rename it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def append_log(path: Path, record: dict) -> None:
    """One JSON object per line. A log write failure never stops a tick."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def cap(seq: Iterable, n: int = SEEN_CAP) -> list:
    """The last ``n`` items, order kept."""
    return list(seq)[-n:]
