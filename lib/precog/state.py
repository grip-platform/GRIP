"""PRECOG W4 state shim — opt-in flag + current-IDR pointer + flag window.

Provides:
  * write_current_idr(idr) — atomic-write the latest IDR JSON to
    ``state/precog/current.idr.json`` so consumers (router, adapter) can read
    the most recent dispatch decision without traversing the chain file.
  * read_current_idr() — read the pointer file; returns None on miss/error.
  * is_precog_enabled() — boolean resolver consulting (1) ``PRECOG_ENABLED``
    env var, (2) the flag-window state file, (3) default OFF.
  * open_flag_window(hours) / close_flag_window() / flag_window_status() —
    operator CLI surface (used by ``bin/precog-window``).

Design notes
------------
* The opt-in flag is THREE-LAYER: env > state > default. The env-var path is
  V>>'s panic-button (manual override always wins). The state file is the
  TTL'd "set the flag to ON for the upcoming hours" pattern V>> ratified at
  the Q4 review — bounded by an explicit ``expires_at`` so default reverts
  to OFF without operator action.
* Atomic write via tmp + os.replace (same pattern as ``lib/precog/replay``).
* Fail-open on every read path — a missing/corrupt state file MUST resolve
  to "disabled" (safety property; we never want a parse error to silently
  enable precog routing).
* Path resolution via ``lib.claude_dir.get_claude_dir`` per
  ``rules/path-portability.md``.

Falsifier
---------
Wrong if (a) env-var override fails to win over state-file enablement, or
(b) an expired flag window reports enabled, or (c) a missing/corrupt state
file reports enabled. Tests in ``tests/test_precog_state.py`` anchor all
three. Mutation that flips any default to enabled MUST fail the suite.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from lib.claude_dir import get_claude_dir

__all__ = [
    "write_current_idr",
    "read_current_idr",
    "is_precog_enabled",
    "open_flag_window",
    "close_flag_window",
    "flag_window_status",
    "STATE_DIR",
    "CURRENT_IDR_PATH",
    "FLAG_WINDOW_PATH",
]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

STATE_DIR: Path = get_claude_dir() / "state" / "precog"
CURRENT_IDR_PATH: Path = STATE_DIR / "current.idr.json"
FLAG_WINDOW_PATH: Path = STATE_DIR / "flag-window.json"

_ENV_VAR = "PRECOG_ENABLED"


# ---------------------------------------------------------------------------
# Current-IDR pointer (consumer-facing)
# ---------------------------------------------------------------------------

def _atomic_write_text(path: Path, text: str) -> bool:
    """Atomic write via a UNIQUE mkstemp tmp + os.replace (fail-open).

    Each writer gets its OWN tmp, so concurrent sessions writing the same shared
    precog state file can't clobber a shared fixed ``.json.tmp`` mid-write — the
    per-session precog-orchestrate hook means these paths ARE multi-writer
    (AGORA #3462 #6). Preserves the caller's exact serialization. Returns True on
    success, False on OSError — never raises (precog writes must not break dispatch).
    """
    import tempfile
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    except OSError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def write_current_idr(idr_dict: dict[str, Any]) -> Path | None:
    """Atomic-write the IDR dict to ``CURRENT_IDR_PATH``.

    Called by ``orchestrate()`` after each emit so consumers (router,
    adapter) can read the most recent decision without traversing the
    chain file. Never raises — a write failure must not break dispatch.

    Returns the persisted Path on success, ``None`` on OSError. Uses a unique
    tmp per write (``_atomic_write_text``) so concurrent sessions don't clobber.
    """
    if _atomic_write_text(CURRENT_IDR_PATH, json.dumps(idr_dict, sort_keys=True)):
        return CURRENT_IDR_PATH
    return None


def read_current_idr() -> dict[str, Any] | None:
    """Read the current-IDR pointer. Returns None if missing/corrupt/empty."""
    if not CURRENT_IDR_PATH.exists():
        return None
    try:
        raw = CURRENT_IDR_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


# ---------------------------------------------------------------------------
# Opt-in flag (three-layer resolver: env > state > default OFF)
# ---------------------------------------------------------------------------

def is_precog_enabled(now: float | None = None) -> bool:
    """Resolve whether PRECOG routing is active.

    Order: explicit env var ``PRECOG_ENABLED`` wins; otherwise the
    flag-window state file (if present and unexpired) decides; otherwise
    default OFF.

    ``now`` injectable for tests; defaults to ``time.time()``.

    Truth values for env var: ``1``, ``true``, ``yes``, ``on`` (case-insensitive).
    Any other env value → disabled (treats accidental ``PRECOG_ENABLED="0"`` /
    ``PRECOG_ENABLED="false"`` as OFF, never as parse-error → silently-ON).
    """
    env = os.environ.get(_ENV_VAR)
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on"}
    window = _read_flag_window()
    if window is None:
        return False
    if not window.get("enabled"):
        return False
    expires_at = window.get("expires_at", 0)
    if not isinstance(expires_at, (int, float)):
        return False
    current = now if now is not None else time.time()
    return current < float(expires_at)


def open_flag_window(hours: float, reason: str = "") -> dict[str, Any]:
    """Mark the flag window OPEN for ``hours`` hours from now.

    Operator pattern: ``bin/precog-window open --hours 4 --reason "W4 test"``.
    The window MUST have a positive duration; zero or negative ``hours``
    raises ``ValueError`` to prevent accidental no-op opens.
    """
    if hours <= 0:
        raise ValueError(f"hours must be > 0, got {hours!r}")
    now = time.time()
    expires_at = now + hours * 3600
    record = {
        "enabled": True,
        "opened_at": now,
        "expires_at": expires_at,
        "hours": hours,
        "reason": reason or "no reason given",
    }
    _write_flag_window(record)
    return record


def close_flag_window() -> dict[str, Any]:
    """Mark the flag window CLOSED — flips ``enabled`` to false immediately."""
    record = {"enabled": False, "closed_at": time.time()}
    _write_flag_window(record)
    return record


def flag_window_status(now: float | None = None) -> dict[str, Any]:
    """Plain-language status for the banner / CLI / session-start hook.

    Returns a dict with keys:
      * ``enabled`` (bool) — same as ``is_precog_enabled``
      * ``source`` (str)   — "env" / "window" / "default"
      * ``window`` (dict | None) — raw window record if present
      * ``remaining_seconds`` (float | None) — seconds until window expires
    """
    env = os.environ.get(_ENV_VAR)
    if env is not None:
        # Env override wins, but still surface the underlying window record
        # (if any) so the operator can see "your env var is masking an
        # otherwise-open window" rather than the window silently vanishing
        # from the status surface. ``remaining_seconds`` stays None because
        # the window is not what's driving the verdict.
        return {
            "enabled": env.strip().lower() in {"1", "true", "yes", "on"},
            "source": "env",
            "window": _read_flag_window(),
            "remaining_seconds": None,
        }
    window = _read_flag_window()
    if window is None:
        return {"enabled": False, "source": "default", "window": None, "remaining_seconds": None}
    current = now if now is not None else time.time()
    expires_at = float(window.get("expires_at", 0)) if isinstance(window.get("expires_at"), (int, float)) else 0
    enabled = bool(window.get("enabled")) and current < expires_at
    remaining = max(0.0, expires_at - current) if expires_at else None
    return {
        "enabled": enabled,
        "source": "window",
        "window": window,
        "remaining_seconds": remaining,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _read_flag_window() -> dict[str, Any] | None:
    """Read the flag-window state file. Returns None if missing/corrupt."""
    if not FLAG_WINDOW_PATH.exists():
        return None
    try:
        raw = FLAG_WINDOW_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_flag_window(record: dict[str, Any]) -> Path | None:
    """Atomic-write the flag-window state file.

    Parity with ``write_current_idr``: fail-open on OSError, return the
    persisted Path on success or ``None`` on failure. The CLI surface
    (``bin/precog-window``) reads back the state via ``flag_window_status``
    after every write, so a None return surfaces to the operator as
    ``enabled=False, source="default"`` — making disk failures visible
    without raising mid-CLI.
    """
    if _atomic_write_text(FLAG_WINDOW_PATH, json.dumps(record, sort_keys=True, indent=2)):
        return FLAG_WINDOW_PATH
    return None
