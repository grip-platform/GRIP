"""WhatsApp control-command parser.

Public API:

    from lib.wa_control_commands import parse, ControlCommand

    cmd = parse("GRIP: pause 2h")
    # → ControlCommand(kind="pause", params={"duration_seconds": 7200})

    cmd = parse("hello GRIP")
    # → None (not a control command)

Recognised commands (case-insensitive on the verb after ``GRIP:``):

    GRIP: pause <N><h|m>   → pause turn-handling for N hours/minutes
    GRIP: mute             → suppress all GRIP outbound until "resume"
    GRIP: opt-out          → POPIA/GDPR purge of per-contact memory
    GRIP: resume           → undo mute/pause (clear pause-until, set active)
    GRIP: status           → return current control state
    GRIP: rc on|off|status → arm/disarm operator-session resume routing
                             (operator-only — enforced by the DISPATCHER via
                             the authenticated sender ID, not by this parser)

The ``GRIP:`` prefix is REQUIRED and case-insensitive. Whitespace tolerant.
Any text NOT matching the grammar returns ``None`` — i.e. it is a regular
turn for the relay to route to ``claude -p`` (NOT a control command).

Falsifier
---------
This module is wrong if the grammar drifts: e.g. accepts ``GRIP pause 2h``
without the colon, or rejects ``GRIP:  PAUSE   2H`` with extra whitespace.
Verifier: ``tests/test_wa_control_commands.py`` — Goodhart-resistant cases.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

__all__ = ["ControlCommand", "parse", "ParseError", "is_grip_attempt"]


def is_grip_attempt(message: str) -> bool:
    """Return True if ``message`` looks like a control-command attempt —
    i.e. starts with ``GRIP:`` (case-insensitive, whitespace tolerant) —
    even when :func:`parse` returned ``None`` because the sub-command
    was malformed (e.g. ``"GRIP: pause 0h"``).

    The listener's handle_message gate uses this to silently drop
    NON-GRIP messages (V>> 2026-05-28 — a contact's natural conversation
    must not trigger HAL inference). Without this guard the listener
    spawns an expensive infer call on every allowlisted text.
    """
    if not message:
        return False
    return _PREFIX_RE.match(message) is not None

# ---------------------------------------------------------------------------
# Grammar constants (closed vocabulary — anti-injection)
# ---------------------------------------------------------------------------

# Recognised command verbs (lowercase canonical form)
_VERBS = frozenset(["pause", "mute", "opt-out", "resume", "status", "rc"])

# rc actions (closed vocabulary — anything else is malformed → None)
_RC_ACTIONS = frozenset(["on", "off", "status"])

# Duration: ``<digits><h|m>`` (hours or minutes). Bounded 1-9999 to refuse
# pathological values like ``GRIP: pause 99999999h``. Falsifier hooks here:
# a regex that accepts unbounded digits would let a single ack DOS the relay.
_DURATION_RE = re.compile(r"^(?P<n>\d{1,4})\s*(?P<unit>[hm])$", re.IGNORECASE)

# Top-level prefix: ``GRIP:`` (case-insensitive, leading/trailing ws OK).
# Captures everything after the colon as the command body.
_PREFIX_RE = re.compile(r"^\s*grip\s*:\s*(?P<body>.+?)\s*$",
                        re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlCommand:
    """A parsed control command.

    Attributes:
        kind: One of {"pause", "mute", "opt-out", "resume", "status"}.
        params: Command-specific parameters. For ``pause`` carries
            ``duration_seconds: int``. Empty dict for parameter-less verbs.
    """
    kind: str
    params: dict = field(default_factory=dict)


class ParseError(ValueError):
    """Raised only by the strict variant ``parse_strict``. ``parse`` returns None."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse(message: str) -> Optional[ControlCommand]:
    """Best-effort parse. Returns None for non-control messages or malformed.

    Use this in the relay dispatcher hot-path — non-control messages MUST
    return None cleanly (they flow on to the regular claude -p spawn).
    """
    if not isinstance(message, str) or not message.strip():
        return None
    m = _PREFIX_RE.match(message)
    if not m:
        return None
    body = m.group("body").strip()
    return _parse_body(body)


def parse_strict(message: str) -> ControlCommand:
    """Strict parse. Raises ``ParseError`` for any failure.

    Use in test surfaces / wizard / CLI where ambiguity should be loud.
    """
    cmd = parse(message)
    if cmd is None:
        raise ParseError(f"Not a valid GRIP control command: {message!r}")
    return cmd


# ---------------------------------------------------------------------------
# Internals (kept small — KISS, CC<30)
# ---------------------------------------------------------------------------


def _parse_body(body: str) -> Optional[ControlCommand]:
    """Parse the body (text after ``GRIP:``). Returns None on any mismatch."""
    parts = body.split(None, 1)
    if not parts:
        return None
    verb = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if verb not in _VERBS:
        return None

    if verb == "pause":
        return _parse_pause(arg)

    if verb == "rc":
        return _parse_rc(arg)

    # Parameter-less verbs MUST have no trailing argument.
    if arg:
        return None
    return ControlCommand(kind=verb, params={})


def _parse_rc(arg: str) -> Optional[ControlCommand]:
    """Parse ``rc on|off|status``. Closed action vocabulary; a bare ``rc`` or
    an unknown action returns None (falls through as a regular turn) — the
    same malformed-argument contract as ``pause``."""
    action = arg.strip().lower()
    if action not in _RC_ACTIONS:
        return None
    return ControlCommand(kind="rc", params={"action": action})


def _parse_pause(arg: str) -> Optional[ControlCommand]:
    """Parse ``pause <N><h|m>``. Returns None if duration is malformed."""
    if not arg:
        return None
    m = _DURATION_RE.match(arg)
    if not m:
        return None
    n = int(m.group("n"))
    if n <= 0:
        return None
    unit = m.group("unit").lower()
    seconds = n * (3600 if unit == "h" else 60)
    return ControlCommand(kind="pause", params={"duration_seconds": seconds})
