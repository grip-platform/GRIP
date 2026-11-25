"""Backlog provenance marker — state-file pattern (NOT env var).

Mirrors the yolo-mode marker design (lib/gates/yolo_mode.py):
  - Atomic write via tempfile + rename (crash-safe)
  - Fail-closed on parse failure
  - TTL-bounded (stale marker = inactive = gate DENIES)
  - Read fresh on every gate call (no module-level cache)

Audit log: ~/.claude/logs/backlog-provenance.jsonl
State file: ~/.claude/state/session/backlog-provenance.json

Schema:
  {
    "operator": str,        # who set it (e.g. "V>>")
    "reason": str,          # why (e.g. "W3 Pages publish approval")
    "scope": str,           # what it unlocks ("pages-publish" | "any")
    "set_at": ISO8601,
    "ttl_seconds": int,
    "deadline": ISO8601
  }

Non-yolo-relaxable: this gate must keep firing even under yolo_l1 bypass
because it guards shared-public repo writes — the same class as force-push
and secrets exposure (permanently hard-blocked in yolo_mode.py).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
MARKER_PATH = _CLAUDE_DIR / "state" / "session" / "backlog-provenance.json"
AUDIT_LOG = _CLAUDE_DIR / "logs" / "backlog-provenance.jsonl"
MAX_TTL_SECONDS = 24 * 3600  # 24h ceiling


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")


def read_marker() -> Optional[dict]:
    """Read and validate the provenance marker. Returns None if absent/stale/malformed."""
    try:
        raw = MARKER_PATH.read_text()
        marker = json.loads(raw)
        deadline = datetime.fromisoformat(marker["deadline"])
        if _now() > deadline:
            return None  # expired → gate denies
        return marker
    except Exception:
        return None  # fail-closed


def set_marker(operator: str, reason: str, scope: str = "any",
               ttl_seconds: int = 3600) -> dict:
    """Write a provenance marker atomically. Returns the marker dict."""
    if ttl_seconds > MAX_TTL_SECONDS:
        # Do not silently truncate a caller's requested duration — a marker
        # that expires sooner than asked is a correctness surprise.
        print(
            f"[backlog-provenance] requested ttl_seconds={ttl_seconds} exceeds "
            f"MAX_TTL_SECONDS={MAX_TTL_SECONDS}; capping — marker will expire "
            f"sooner than requested",
            file=sys.stderr,
        )
    ttl_seconds = min(ttl_seconds, MAX_TTL_SECONDS)
    now = _now()
    marker = {
        "operator": operator,
        "reason": reason,
        "scope": scope,
        "set_at": _iso(now),
        "ttl_seconds": ttl_seconds,
        "deadline": _iso(now + timedelta(seconds=ttl_seconds)),
    }
    MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tempfile in same dir → rename
    fd, tmp = tempfile.mkstemp(dir=MARKER_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(marker, fh)
        os.replace(tmp, MARKER_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise
    _audit({"action": "set", "marker": marker})
    return marker


def clear_marker() -> None:
    """Remove the provenance marker (gate returns to DENY state)."""
    try:
        MARKER_PATH.unlink(missing_ok=True)
        _audit({"action": "clear"})
    except Exception as exc:
        # Loud, not silent: a marker that fails to clear leaves the gate in
        # the WRONG state (still permissive). The operator must see this.
        print(
            f"[backlog-provenance] clear_marker FAILED ({exc!r}) — marker may "
            f"still be active at {MARKER_PATH}; verify manually",
            file=sys.stderr,
        )


def _audit(record: dict) -> None:
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with AUDIT_LOG.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        # NEVER silently swallow a provenance-audit failure (lost record of
        # who authorised a marker). Do NOT re-raise — marker set/clear must
        # still succeed even if its audit write fails.
        print(
            f"[backlog-provenance] AUDIT WRITE FAILED ({exc!r}) — marker "
            f"action UNLOGGED at {AUDIT_LOG}",
            file=sys.stderr,
        )
