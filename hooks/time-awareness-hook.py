#!/usr/bin/env python3
"""Time-Awareness Hook (closes #2568)

UserPromptSubmit hook: injects a [TIME] system-reminder into every user
turn so the LLM can re-ground its 'current state' references in actual
wall-clock time rather than letting an early-session statement pass
forward as if it remained current.

State: ~/.claude/state/session/time-awareness-start.json
  Created on first invocation per session id (or per host fallback).
  Subsequent invocations read it to compute session-age in minutes.

Output format (single line, prepended via additionalContext or stderr):
  [TIME] Wall clock: 2026-05-04 13:15 SAST | session age: 14m

Falls open on any error — never blocks user input.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

STATE_DIR = os.path.join(os.environ.get("HOME", ""), ".claude", "state", "session")
STATE_FILE = os.path.join(STATE_DIR, "time-awareness-start.json")

# Prompts the harness injects rather than a person typing. UserPromptSubmit
# fires for these too, and its input carries no origin field: the installed
# build (checked 2026-09-29) passes only the common fields, `hook_event_name`,
# `prompt` and `session_title`. So the prompt text is the only discriminator.
# These are the wrappers the harness puts around them; the transcript records
# the same prompts with `origin.kind` "task-notification" and "peer" (GRIP#6675).
# The peer tag must be followed by whitespace or `>`: the real wrapper always
# carries a `from` attribute, and a bare prefix would also swallow typed text
# such as "<cross-session-messages ...".
_MACHINE_PROMPT = re.compile(r"<task-notification>|<cross-session-message[\s>]")


def typed_by_a_human(prompt: object) -> bool:
    """False only when the prompt, after its leading whitespace is stripped,
    opens with a harness wrapper.

    A missing or non-string prompt counts as typed, so a payload without the
    field keeps the old behaviour. A prompt that merely mentions a wrapper
    mid-sentence is typed text and counts as typed.
    """
    return not (isinstance(prompt, str) and _MACHINE_PROMPT.match(prompt.lstrip()))


def _load_or_create_start(session_id: str, now: float) -> float:
    """Return session_start_epoch. Create state file on first call."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("session_id") == session_id and "start_epoch" in data:
            return float(data["start_epoch"])
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        pass

    os.makedirs(STATE_DIR, exist_ok=True)
    payload = {"session_id": session_id, "start_epoch": now}
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return now


def _stamp_prompt(session_id: str, now: float) -> None:
    """Record that a human typed into this session just now. Never raises.

    THE GAP THIS FILLS. This hook already maintains a session-START file, so
    GRIP could say how old a session was and could not say whether anybody was
    still at the keyboard. Those are different questions, and the turn-end hold
    needs the second one: on 2026-09-22 it held a session for twenty minutes
    while V>>'s own `/afk` marker was live, because the only evidence it
    consulted was an environment variable about headlessness.

    A prompt-submit hook is the one place that fact is directly observable, so
    the stamp is taken here and read by `lib.operator_presence`. But reaching
    this hook does NOT by itself mean a human pressed enter: a background task's
    completion notice and a message from another session fire it too. Measured
    2026-09-29 (GRIP#6675): with no human prompt after 02:40:37 UTC, a task
    notice moved the stamp from 02:44:39 to 02:55:03. So `main` stamps only
    when `typed_by_a_human` says so.

    Swallowing every failure is deliberate: this hook must never block input,
    and a missing stamp reads as ABSENT downstream, which is the harmless
    direction (the turn ends, the question parks, nothing is held).
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from lib.operator_presence import record_prompt

        record_prompt(session_id, now=now)
    except Exception:
        pass


def _format_age(seconds: float) -> str:
    """Format session age as Xh Ym or Ym."""
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"


def build_reminder(now: float, start: float) -> str:
    """Compose the [TIME] system-reminder string."""
    iso_utc = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    age = _format_age(now - start)
    return f"[TIME] Wall clock: {iso_utc} | session age: {age}"


def main() -> int:
    try:
        event = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (json.JSONDecodeError, ValueError):
        event = {}

    session_id = event.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or "default"
    now = time.time()

    if typed_by_a_human(event.get("prompt")):
        _stamp_prompt(session_id, now)

    try:
        start = _load_or_create_start(session_id, now)
    except OSError:
        start = now  # fall open: never block input

    reminder = build_reminder(now, start)
    # additionalContext is the documented UserPromptSubmit injection channel.
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                              "additionalContext": reminder}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
