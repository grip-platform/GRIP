"""Tell already-running sessions that what they loaded is out of date.

THE MEASURED FACT THIS EXISTS FOR

All twelve launchable harnesses on this machine -- claude, codex, aider,
opencode, hermes, agy, gemini, dsh, kimi-code, qwen, continue, kilo -- resolve
to the SAME tree (`CLAUDE_DIR="$HOME/.claude"`, bin/grip:1269). So a change is
on disk for every one of them the instant it lands.

That is exactly why a signal looks redundant, and exactly why it is not: a
session reads its rules ONCE, at SessionStart. A running session never re-reads.
So the shared tree does not distribute a change to anything already running --
it distributes it to the NEXT session to boot, and leaves every live one frozen
at whatever it loaded, with no way to discover that it is behind.

The signal therefore does not carry content. Content is already on disk. It
carries the one fact disk cannot: YOUR LOADED STATE IS STALE.

WHY A FILE AND NOT A DAEMON

A daemon speaks a protocol, and twelve harnesses do not share one. Every harness
on this machine can read a file, so a file is the only lingua franca that needs
no adapter per harness and no process to stay alive. `grip-presence` is the
counter-example: it is an MCP server, so it can only see MCP clients, which is
why a broadcast to "24 sessions" reached zero Codex or OpenCode sessions.

WHY A GENERATION NUMBER AND NOT A TIMESTAMP

A session needs to answer "am I behind?" without trusting its own clock, without
comparing to a peer, and without a handshake. A monotonically increasing integer
makes staleness a comparison between two numbers the session already holds: the
one it booted at, and the one on disk now. Timestamps invite clock skew across
machines and tempt a reader into "recent enough", which is the judgement this is
designed to remove.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

_LIB = Path(__file__).resolve().parent
import sys
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from claude_dir import get_claude_dir  # noqa: E402

# One file, in the tree every harness already resolves to. Not under state/
# session namespacing: this is deliberately GLOBAL, because its whole purpose is
# to be visible to a harness that knows nothing about this session.
SIGNAL_RELPATH = "state/harness-signal.json"

# What a signal asks of a reader. Ordered by how much it costs the reader, so a
# reader can decide whether to act now or at its next natural break.
SEVERITIES = ("fyi", "reload", "restart")

# A reader that has never recorded a generation is treated as generation 0,
# which is behind any real signal. Unknown must mean STALE, never fresh --
# the same None-is-not-False discipline the fan-out preflight uses: "I could
# not tell" and "I am current" lead to opposite actions.
UNKNOWN_GENERATION = 0


class SignalError(Exception):
    """Raised when a signal cannot be read or written honestly."""


def signal_path(claude_dir: Optional[Path] = None) -> Path:
    return (claude_dir or get_claude_dir()) / SIGNAL_RELPATH


def read_signal(claude_dir: Optional[Path] = None) -> dict:
    """The current signal, or a generation-0 blank. Never raises on absence.

    Absence is a legitimate state -- no one has ever raised a signal -- and it
    must be distinguishable from a corrupt file, which is not.
    """
    p = signal_path(claude_dir)
    if not p.is_file():
        return {"generation": UNKNOWN_GENERATION, "reason": "", "severity": "fyi",
                "raised_at": None, "raised_by": "", "action": ""}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SignalError(f"signal file at {p} is unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("generation"), int):
        raise SignalError(f"signal file at {p} has no integer generation")
    return data


def raise_signal(
    reason: str,
    *,
    severity: str = "reload",
    action: str = "",
    raised_by: str = "",
    claude_dir: Optional[Path] = None,
) -> dict:
    """Bump the generation and say what changed. Returns the new signal.

    The bump is read-then-write rather than an atomic increment, which is a real
    race between two sessions raising at once. It is accepted deliberately: the
    consequence of losing a bump is that two changes share one generation, so a
    reader is told to reload once instead of twice -- and reloading once when two
    things changed is correct behaviour, not a defect. A lock would add a failure
    mode worse than the race it removes.
    """
    if severity not in SEVERITIES:
        raise SignalError(f"severity must be one of {SEVERITIES}, got {severity!r}")
    if not reason.strip():
        raise SignalError("a signal with no reason tells a reader nothing; give one")

    current = read_signal(claude_dir)
    payload = {
        "generation": int(current["generation"]) + 1,
        "reason": reason.strip(),
        "severity": severity,
        "action": action.strip(),
        "raised_by": raised_by or os.environ.get("CLAUDE_SESSION_ID", "unknown"),
        "raised_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    p = signal_path(claude_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)  # atomic on POSIX, so a reader never sees a half-written file
    return payload


def is_stale(my_generation: int, claude_dir: Optional[Path] = None) -> dict:
    """Is a reader holding `my_generation` behind the tree? Says why either way.

    This is the whole reader-side API, and it is one integer comparison on
    purpose: any harness in any language can implement it against the same file
    without importing anything of GRIP's.
    """
    current = read_signal(claude_dir)
    gen = int(current["generation"])
    behind = gen - int(my_generation)
    return {
        "stale": behind > 0,
        "my_generation": int(my_generation),
        "current_generation": gen,
        "behind_by": max(0, behind),
        "severity": current.get("severity", "fyi") if behind > 0 else None,
        "reason": current.get("reason", "") if behind > 0 else "",
        "action": current.get("action", "") if behind > 0 else "",
    }


# ---------------------------------------------------------------------------
# Acknowledgement. Added after a council seat pointed out that the signal above
# is send-only, which reproduces the exact failure that motivated it.
#
# grip-presence reported a broadcast reaching "24 sessions". Roughly five were
# live and none were non-Claude-Code. Nothing verified reach, so the number was
# a claim about an attempt dressed as a fact about delivery. A staleness signal
# with no receipt is that same shape: the raiser learns nothing about whether
# any session acted, and `declared-input-must-reach-the-actor.md` calls a signal
# nobody acknowledges a declared input that never reached its actor.
#
# The ack is deliberately NOT a registry -- nobody maintains it, and no service
# owns it. It is one line the READING harness appends for itself, in the same
# file discipline as the signal, so a Codex session can acknowledge with an
# append and nothing else.
# ---------------------------------------------------------------------------

ACKS_RELPATH = "state/harness-signal-acks.jsonl"


def acks_path(claude_dir: Optional[Path] = None) -> Path:
    return (claude_dir or get_claude_dir()) / ACKS_RELPATH


def ack_signal(
    generation: int,
    *,
    harness: str,
    session_id: str = "",
    claude_dir: Optional[Path] = None,
) -> dict:
    """Record that THIS harness saw generation N and acted. Append-only.

    Append-only rather than a keyed write because two harnesses acknowledging at
    once must not be able to lose each other's line, and because the history of
    who acted when is the evidence -- collapsing it to a latest-wins map would
    discard exactly the record that makes reach checkable.
    """
    if not harness.strip():
        raise SignalError("an ack with no harness cannot answer 'who acted'")
    entry = {
        "generation": int(generation),
        "harness": harness.strip(),
        "session_id": session_id or os.environ.get("CLAUDE_SESSION_ID", ""),
        "acked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    p = acks_path(claude_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def reach(generation: int, claude_dir: Optional[Path] = None) -> dict:
    """Who acknowledged generation N. The answer grip-presence could not give.

    Reports the harnesses that acted, NOT a count of sessions messaged. A
    malformed line is skipped and counted rather than raising: one corrupt
    append must not make every other harness's ack unreadable, but silently
    dropping it would overstate nothing and understate reach, so the count is
    returned for the caller to see.
    """
    p = acks_path(claude_dir)
    if not p.is_file():
        return {"generation": int(generation), "harnesses": [], "acks": 0, "unreadable_lines": 0}
    harnesses, acks, bad = set(), 0, 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if int(row["generation"]) == int(generation):
                harnesses.add(str(row["harness"]))
                acks += 1
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            bad += 1
    return {
        "generation": int(generation),
        "harnesses": sorted(harnesses),
        "acks": acks,
        "unreadable_lines": bad,
    }
