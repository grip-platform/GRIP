"""P2 W0 — /holistic provenance DENY-gate marker (the build-order inversion).

plans/unified-holistic-command.md W0. The binding-DA dissent forbids
building the thin facade first: the hazard is *a cross-session capability
existing under a tempting name* that a future deadline-pressured session
wires into direct foreign mutation. Correct-by-construction ⇒ the
mechanical DENY gate is built + tested BEFORE any code that could be
tempted to violate it.

This module is ONLY the provenance marker (read/write/TTL). The PreToolUse
hook (hooks/holistic-provenance-gate.py) consumes it: while a `/holistic`
provenance marker is ACTIVE, DENY any `closeout.py` invocation OR
`git (commit|push|checkout|switch|reset|rebase)`. `/holistic` provably
needs none of those (it only regenerates a file, writes markers,
dispatches a bg agent) — so denying ALL git-mutation + closeout under its
provenance is total, simple, unbypassable.

Rule 21: mirrors the proven #1867 `session_isolation` /
`yolo_mode` state-file marker pattern (state-file NOT env — the hook
subprocess cannot see a Bash-tool inline env; that lesson was burned
twice). PARAMOUNT, NOT yolo-relaxable (mirrors no-guardrail-evasion: a
cross-session-safety gate is never the autonomous fast-path).

Fail-safe direction: marker absent / stale / unparyable ⇒ provenance
INACTIVE ⇒ gate ALLOWS (normal sessions unaffected). The gate only
restricts WHILE `/holistic` explicitly runs.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
MARKER_PATH = _CLAUDE_DIR / "state" / "session" / "holistic-active.json"
AUDIT_LOG = _CLAUDE_DIR / "logs" / "holistic-provenance-gate.jsonl"
MAX_TTL_SECONDS = 4 * 3600  # /holistic runs are short; 4h hard ceiling


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")


def read_provenance() -> Optional[dict]:
    """Return the active marker, or None if absent/stale/malformed.

    None ⇒ provenance INACTIVE ⇒ the gate ALLOWS (fail-safe: a parse error
    must NOT wrongly block a normal session's git)."""
    try:
        marker = json.loads(MARKER_PATH.read_text())
        if _now() > datetime.fromisoformat(marker["deadline"]):
            return None  # expired ⇒ inactive
        return marker
    except Exception:
        return None  # fail-safe: absent/stale/corrupt ⇒ inactive


def is_holistic_active() -> bool:
    """True ⇒ the DENY gate is armed (git-mutation + closeout blocked)."""
    return read_provenance() is not None


def set_provenance(reason: str, *, operator: str = "/holistic",
                   ttl_seconds: int = 1800) -> dict:
    """Arm the gate for the duration of a /holistic run (atomic write)."""
    ttl_seconds = min(ttl_seconds, MAX_TTL_SECONDS)
    now = _now()
    marker = {
        "operator": operator,
        "reason": reason,
        "pid": os.getpid(),                    # pid-scoped
        "set_at": _iso(now),
        "ttl_seconds": ttl_seconds,
        "deadline": _iso(now + timedelta(seconds=ttl_seconds)),
    }
    MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=MARKER_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(marker, fh)
        os.replace(tmp, MARKER_PATH)           # atomic
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise
    _audit({"action": "set", "marker": marker})
    return marker


def clear_provenance() -> None:
    """Disarm the gate (end of a /holistic run). Loud-not-silent on
    failure: a marker that fails to clear leaves the gate wrongly armed
    (blocking the owning session's later git) — the operator must see it."""
    try:
        MARKER_PATH.unlink(missing_ok=True)
        _audit({"action": "clear"})
    except OSError as exc:
        import sys
        print(f"[holistic-provenance] clear FAILED ({exc!r}) — gate may "
              f"stay armed at {MARKER_PATH}; verify manually", file=sys.stderr)


def _audit(record: dict) -> None:
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with AUDIT_LOG.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        import sys
        print(f"[holistic-provenance] AUDIT WRITE FAILED ({exc!r}) — "
              f"action UNLOGGED at {AUDIT_LOG}", file=sys.stderr)
