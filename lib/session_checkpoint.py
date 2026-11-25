#!/usr/bin/env python3
"""
Session Checkpoint — canonical atomic checkpoint primitive (Seam C).

Shared by all channel adapters: Slack-thread, Discord-thread, WA-1to1, WA-Group.
Mirrors the atomic-write pattern from lib/ultrado_checkpoint.py but generalised
to arbitrary session keys and the unified Checkpoint schema.

Origin: council verdict 2026-05-03 (commit 4103db49), Collapse 2.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

CLAUDE_DIR = Path.home() / ".claude"
THREADS_DIR = CLAUDE_DIR / "state" / "threads"
CHECKPOINT_VERSION = 1


@dataclass
class Checkpoint:
    key: str                          # composite session key, e.g. "C123:1.2" or "thread-id"
    owner_id: str                     # platform user-id of the thread owner
    parent_id: str                    # parent channel / group id
    platform: str                     # "slack" | "discord" | "whatsapp" | …
    last_activity_at: str             # ISO-8601 UTC
    turns: list[dict[str, Any]] = field(default_factory=list)
    pending_todos: list[str] = field(default_factory=list)
    bootstrap_digest: str = ""        # hash of the bootstrap context (idempotency guard)
    commander_id: Optional[int] = None
    version: int = CHECKPOINT_VERSION


@dataclass
class ResumeContext:
    checkpoint: Optional[Checkpoint]
    is_fresh: bool  # True → no prior checkpoint; caller must bootstrap


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def checkpoint(key: str, data: Checkpoint) -> None:
    """Atomically write a checkpoint for *key*.

    Uses tempfile + os.replace for crash-safety: a partial write can never
    corrupt the existing checkpoint file.
    """
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    target = _path_for(key)
    payload = asdict(data)

    fd, tmp = tempfile.mkstemp(dir=THREADS_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def resume_or_bootstrap(key: str) -> ResumeContext:
    """Return existing checkpoint if present and version-compatible, else fresh context."""
    path = _path_for(key)
    if not path.exists():
        return ResumeContext(checkpoint=None, is_fresh=True)

    try:
        with open(path) as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return ResumeContext(checkpoint=None, is_fresh=True)

    if data.get("version", 0) != CHECKPOINT_VERSION:
        return ResumeContext(checkpoint=None, is_fresh=True)

    try:
        cp = Checkpoint(**{k: data[k] for k in Checkpoint.__dataclass_fields__ if k in data})
        return ResumeContext(checkpoint=cp, is_fresh=False)
    except (TypeError, KeyError):
        return ResumeContext(checkpoint=None, is_fresh=True)


def evict(key: str) -> bool:
    """Remove a checkpoint from disk. Returns True if it existed."""
    path = _path_for(key)
    if path.exists():
        path.unlink()
        return True
    return False


def list_keys() -> list[str]:
    """Return all active session keys (file stems under THREADS_DIR)."""
    if not THREADS_DIR.exists():
        return []
    return [p.stem for p in THREADS_DIR.glob("*.json")]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _path_for(key: str) -> Path:
    """Convert a session key to a safe filename under THREADS_DIR.

    Replaces filesystem-unsafe chars with underscores so 'C123:1.2' becomes
    'C123_1.2.json'.
    """
    safe = key.replace("/", "_").replace(":", "_").replace("..", "_")
    return THREADS_DIR / f"{safe}.json"
