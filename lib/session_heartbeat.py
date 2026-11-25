#!/usr/bin/env python3
"""
session_heartbeat.py -- Lightweight session coordination via heartbeat file.

Sessions register themselves and periodically heartbeat. Other sessions
can discover active peers. Stale sessions are automatically pruned.

Storage: cache/session-heartbeats.json (fcntl advisory locked)

Usage:
    from lib.session_heartbeat import register_session, heartbeat, get_active_sessions

    sid = register_session(project_path="~/.claude")
    heartbeat(sid)
    active = get_active_sessions()
    deregister_session(sid)
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
try:
    from _compat import fcntl  # POSIX fcntl, or the LockFileEx lock on Windows
except ImportError:  # imported/run as lib.X, so lib/ is not itself on sys.path
    from lib._compat import fcntl

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
HEARTBEAT_PATH = CLAUDE_DIR / "cache" / "session-heartbeats.json"
DEFAULT_MAX_AGE = 600  # 10 minutes


def _read_registry() -> dict:
    """Read the heartbeat registry with shared lock."""
    if not HEARTBEAT_PATH.exists():
        return {}
    try:
        with open(HEARTBEAT_PATH) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError):
        return {}


def _write_registry(data: dict) -> None:
    """Write the heartbeat registry with exclusive lock."""
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HEARTBEAT_PATH, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            json.dump(data, f, indent=2)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def register_session(
    project_path: str = "",
    session_id: str = "",
    pid: int = 0,
) -> str:
    """Register a new session. Returns the session ID."""
    if not session_id:
        session_id = uuid.uuid4().hex[:12]
    if not pid:
        pid = os.getpid()

    registry = _read_registry()
    registry[session_id] = {
        "pid": pid,
        "project_path": project_path or os.getcwd(),
        "registered_at": time.time(),
        "last_heartbeat": time.time(),
    }
    _write_registry(registry)
    return session_id


def heartbeat(session_id: str) -> bool:
    """Update heartbeat timestamp. Returns True if session was found."""
    registry = _read_registry()
    if session_id not in registry:
        return False
    registry[session_id]["last_heartbeat"] = time.time()
    _write_registry(registry)
    return True


def get_active_sessions(max_age_sec: int = DEFAULT_MAX_AGE) -> list[dict]:
    """Return list of active (non-stale) sessions."""
    registry = _read_registry()
    now = time.time()
    active = []
    for sid, info in registry.items():
        age = now - info.get("last_heartbeat", 0)
        if age <= max_age_sec:
            active.append({
                "session_id": sid,
                "pid": info.get("pid", 0),
                "project_path": info.get("project_path", ""),
                "age_seconds": round(age, 1),
            })
    return sorted(active, key=lambda x: x["age_seconds"])


def deregister_session(session_id: str) -> bool:
    """Remove a session from the registry. Returns True if found."""
    registry = _read_registry()
    if session_id not in registry:
        return False
    del registry[session_id]
    _write_registry(registry)
    return True


def prune_stale(max_age_sec: int = DEFAULT_MAX_AGE) -> int:
    """Remove stale sessions. Returns count removed."""
    registry = _read_registry()
    now = time.time()
    stale_ids = [
        sid for sid, info in registry.items()
        if now - info.get("last_heartbeat", 0) > max_age_sec
    ]
    for sid in stale_ids:
        del registry[sid]
    if stale_ids:
        _write_registry(registry)
    return len(stale_ids)
