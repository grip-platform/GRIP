#!/usr/bin/env python3
"""
context_watcher.py -- File-watching context subscription.

Simple alternative to WebSocket broadcasting. Session B polls
the delta JSONL file for changes since its last read position.

80% of the value of live broadcasting, 20% of the complexity.

Usage:
    from lib.context_watcher import ContextWatcher

    watcher = ContextWatcher()
    new_deltas = watcher.check()  # returns new deltas since last check
    watcher.subscribe(callback)   # register callback for changes
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DELTA_PATH = CLAUDE_DIR / "cache" / "context-deltas.jsonl"


class ContextWatcher:
    """Polls delta JSONL for new entries since last check."""

    def __init__(self, poll_interval: float = 10.0):
        self.poll_interval = poll_interval
        self._last_position: int = 0
        self._last_mtime: float = 0
        self._callbacks: list[Callable[[list[dict]], None]] = []
        self._initialize_position()

    def _initialize_position(self) -> None:
        """Set initial position to end of file (only watch new entries)."""
        if DELTA_PATH.exists():
            stat = DELTA_PATH.stat()
            self._last_position = stat.st_size
            self._last_mtime = stat.st_mtime

    def check(self) -> list[dict]:
        """Check for new deltas since last read. Returns list of new entries."""
        if not DELTA_PATH.exists():
            return []

        current_mtime = DELTA_PATH.stat().st_mtime
        if current_mtime == self._last_mtime:
            return []

        new_deltas = self._read_new_entries()
        self._last_mtime = current_mtime
        self._notify(new_deltas)
        return new_deltas

    def _read_new_entries(self) -> list[dict]:
        """Read and parse entries from last position onwards."""
        entries: list[dict] = []
        try:
            with open(DELTA_PATH) as f:
                f.seek(self._last_position)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                self._last_position = f.tell()
        except OSError:
            pass
        return entries

    def _notify(self, deltas: list[dict]) -> None:
        """Invoke registered callbacks with new deltas."""
        if not deltas:
            return
        for cb in self._callbacks:
            try:
                cb(deltas)
            except Exception:
                pass

    def subscribe(self, callback: Callable[[list[dict]], None]) -> None:
        """Register a callback for new deltas."""
        self._callbacks.append(callback)

    def unsubscribe(self, callback: Callable[[list[dict]], None]) -> None:
        """Remove a callback."""
        self._callbacks = [cb for cb in self._callbacks if cb is not callback]

    def reset(self) -> None:
        """Reset position to start of file (re-read everything)."""
        self._last_position = 0
        self._last_mtime = 0


def watch_once() -> list[dict]:
    """One-shot check for new deltas (convenience function)."""
    watcher = ContextWatcher()
    watcher.reset()
    return watcher.check()
