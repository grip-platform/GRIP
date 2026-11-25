"""Per-contact WhatsApp control state persistence.

Storage: ``$CLAUDE_DIR/state/wa-scopes/<slug>/control.json``

Schema (version=1)::

    {
      "state": "active" | "paused" | "muted" | "opted_out",
      "pause_until_iso": "2026-05-28T17:42:00Z" | null,
      "updated_at_iso": "2026-05-28T15:42:00Z",
      "version": 1,
      "resumed_at_iso": "2026-06-26T10:00:00Z" | null
    }

Atomicity
---------
Every write goes through a temp-file + ``os.replace`` (POSIX rename is
atomic on the same filesystem). Concurrent writes from multiple relay
workers cannot interleave a partial JSON blob — readers see the OLD or
the NEW, never a half-written file.

Slug derivation
---------------
``slug_for_jid("27000000000@s.whatsapp.net")`` → ``"27000000000"``
``slug_for_jid("000000000000000000@g.us")``       → ``"g_120000000000000"``
Slug is filesystem-safe by construction (digits + ``g_`` prefix for groups).

Falsifier
---------
This module is wrong if a race between two parallel writers produces a
corrupt JSON read (incomplete bytes). Verifier:
``test_wa_control_state_concurrent_writes_are_atomic`` in
``tests/test_wa_control_commands.py``.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

# Path portability — never hardcode user paths (rules/path-portability.md).
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

__all__ = [
    "ControlState",
    "load",
    "save",
    "apply_command",
    "slug_for_jid",
    "is_paused",
    "STATE_ACTIVE",
    "STATE_PAUSED",
    "STATE_MUTED",
    "STATE_OPTED_OUT",
]

# ---------------------------------------------------------------------------
# Constants (closed vocabulary — anti-injection)
# ---------------------------------------------------------------------------

STATE_ACTIVE = "active"
STATE_PAUSED = "paused"
STATE_MUTED = "muted"
STATE_OPTED_OUT = "opted_out"
_VALID_STATES = frozenset(
    [STATE_ACTIVE, STATE_PAUSED, STATE_MUTED, STATE_OPTED_OUT]
)

_SCHEMA_VERSION = 1

# JID validation: ``<digits>@s.whatsapp.net`` (personal) or
# ``<digits>@g.us`` (group). Anything else → ValueError.
_PERSONAL_JID_RE = re.compile(r"^(?P<n>\d{6,20})@s\.whatsapp\.net$")
# WhatsApp's newer privacy-preserving identifier. It is a DIFFERENT namespace
# from a phone number, so it gets its own slug prefix: a LID and a phone
# number that happen to share digits must never share an opt-out record.
_LID_JID_RE = re.compile(r"^(?P<n>\d{6,20})@lid$")
_GROUP_JID_RE = re.compile(r"^(?P<n>\d{8,30})@g\.us$")


# ---------------------------------------------------------------------------
# Data type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlState:
    """Per-contact control state. Frozen for safety; use ``replace`` to mutate."""
    state: str = STATE_ACTIVE
    pause_until_iso: Optional[str] = None
    updated_at_iso: str = ""
    version: int = _SCHEMA_VERSION
    resumed_at_iso: Optional[str] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def slug_for_jid(jid: str) -> str:
    """Derive a filesystem-safe slug from a WhatsApp JID.

    Raises ``ValueError`` for malformed JIDs.
    """
    if not isinstance(jid, str):
        raise ValueError(f"JID must be a string, got {type(jid).__name__}")
    m = _PERSONAL_JID_RE.match(jid)
    if m:
        return m.group("n")
    m = _LID_JID_RE.match(jid)
    if m:
        return f"lid_{m.group('n')}"
    m = _GROUP_JID_RE.match(jid)
    if m:
        return f"g_{m.group('n')}"
    raise ValueError(
        f"Invalid WhatsApp JID {jid!r}. "
        "Expected '<digits>@s.whatsapp.net', '<digits>@lid' or '<digits>@g.us'."
    )


def _state_dir(slug: str) -> Path:
    return get_claude_dir() / "state" / "wa-scopes" / slug


def _state_file(slug: str) -> Path:
    return _state_dir(slug) / "control.json"


def load(jid: str) -> ControlState:
    """Load the control state for a JID. Returns default ACTIVE if file missing.

    Corrupt files fail-safe to ACTIVE but are PRESERVED for forensics:
    a truncated/partial JSON (e.g., from a crash mid-write) is renamed to
    ``control.json.bak-<unix-ts>`` before the default is returned, so the
    bad bytes are kept as evidence and subsequent saves can write cleanly.
    (AGORA PR #3262 round-2 concern 2.)
    """
    slug = slug_for_jid(jid)
    path = _state_file(slug)
    if not path.exists():
        return ControlState(updated_at_iso=_now_iso())
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (ValueError, OSError):
        # Preserve the bad file under a .bak-<ts> suffix before falling back.
        # rename is best-effort — if it fails (e.g., permission), we still
        # return the safe default; the caller can save() over the corrupt file.
        try:
            bak_ts = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
            path.rename(path.with_suffix(f".json.bak-{bak_ts}"))
        except OSError:
            pass
        return ControlState(updated_at_iso=_now_iso())
    state = data.get("state")
    if state not in _VALID_STATES:
        return ControlState(updated_at_iso=_now_iso())
    return ControlState(
        state=state,
        pause_until_iso=data.get("pause_until_iso"),
        updated_at_iso=data.get("updated_at_iso", _now_iso()),
        version=int(data.get("version", _SCHEMA_VERSION)),
        resumed_at_iso=data.get("resumed_at_iso"),
    )


def save(jid: str, state: ControlState) -> None:
    """Atomically persist control state. Temp-file + ``os.replace``."""
    if state.state not in _VALID_STATES:
        raise ValueError(f"Invalid state {state.state!r}")
    slug = slug_for_jid(jid)
    _state_dir(slug).mkdir(parents=True, exist_ok=True)
    path = _state_file(slug)
    tmp = path.with_suffix(f".{os.getpid()}.{os.urandom(4).hex()}.tmp")
    payload = {
        "state": state.state,
        "pause_until_iso": state.pause_until_iso,
        "updated_at_iso": state.updated_at_iso or _now_iso(),
        "version": state.version,
        "resumed_at_iso": state.resumed_at_iso,
    }
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def apply_command(jid: str, command_kind: str,
                  command_params: Optional[dict] = None,
                  now: Optional[_dt.datetime] = None) -> ControlState:
    """Apply a parsed control command, persist, and return the new state.

    ``now`` is injectable for tests; defaults to ``datetime.utcnow()``.
    """
    command_params = command_params or {}
    when = now or _dt.datetime.now(_dt.timezone.utc)
    when_iso = _iso(when)
    current = load(jid)

    new_state = _transition(current, command_kind, command_params, when, when_iso)
    save(jid, new_state)
    return new_state


def is_paused(state: ControlState, now: Optional[_dt.datetime] = None) -> bool:
    """Return True iff the state is PAUSED and the TTL has not elapsed.

    If pause_until_iso is None or missing, treats as not paused (defensive).
    """
    if state.state != STATE_PAUSED or not state.pause_until_iso:
        return False
    when = now or _dt.datetime.now(_dt.timezone.utc)
    try:
        until = _dt.datetime.fromisoformat(state.pause_until_iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    return when < until


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _transition(current: ControlState, kind: str, params: dict,
                when: _dt.datetime, when_iso: str) -> ControlState:
    """Pure-function state transition. CC<10 by design."""
    if kind == "pause":
        seconds = int(params.get("duration_seconds", 0))
        # Defence-in-depth (AGORA PR #3262 concern 3): the parser already
        # rejects non-positive durations, but an attacker-supplied or
        # programmatic dict could bypass the parser. Re-validate here so the
        # state transition cannot produce a vacuous (instantly-expired) pause.
        if seconds <= 0:
            raise ValueError(
                f"pause requires positive duration_seconds, got {seconds!r}"
            )
        until = when + _dt.timedelta(seconds=seconds)
        return replace(current, state=STATE_PAUSED,
                       pause_until_iso=_iso(until), updated_at_iso=when_iso)
    if kind == "mute":
        return replace(current, state=STATE_MUTED,
                       pause_until_iso=None, updated_at_iso=when_iso)
    if kind == "opt-out":
        return replace(current, state=STATE_OPTED_OUT,
                       pause_until_iso=None, updated_at_iso=when_iso)
    if kind == "resume":
        return replace(current, state=STATE_ACTIVE,
                       pause_until_iso=None, updated_at_iso=when_iso,
                       resumed_at_iso=when_iso)
    if kind == "status":
        return current  # no-op; status is read-only
    raise ValueError(f"Unknown command kind: {kind!r}")


def _now_iso() -> str:
    return _iso(_dt.datetime.now(_dt.timezone.utc))


def _iso(dt: _dt.datetime) -> str:
    """Render UTC datetime as ISO-8601 with 'Z' suffix (RFC3339)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
