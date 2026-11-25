#!/usr/bin/env python3
"""UserPromptSubmit hook: advise when GRIP is escalating unrequested adversarial
analysis on personal material.

ADVISORY ONLY, BY CONSTRUCTION -- and this is the safety property, not a setting.
It emits ``additionalContext``. That schema has no field that removes, rewrites,
or filters a response, and the hook runs BEFORE the response exists, so it never
sees the text it is said to be moderating. It cannot suppress a finding because
it has no channel through which suppression is expressible. There is deliberately
NO promotion ladder to DENY: a DENY path would be a suppression channel.

Escape-hatch audit (rules/gate-resolution-escape-hatch.md, Invariant 1): this
blocks nothing, so it cannot deadlock its own resolution.

Detector: lib/unrequested_escalation_detector.py
Rule:     rules/critique-the-artefact-not-the-person.md
Audit:    ~/.claude/logs/unrequested-escalation.jsonl
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))
sys.path.insert(0, str(_CLAUDE_DIR))

from lib.unrequested_escalation_detector import (  # noqa: E402
    Touch, Turn, Window, assess, render_advisory,
)

_AUDIT = _CLAUDE_DIR / "logs" / "unrequested-escalation.jsonl"
_STATE = _CLAUDE_DIR / "state" / "session" / "unrequested-escalation.json"
_WINDOW_TURNS = 8
_PATH_TOOLS = {"Read", "Write", "Edit", "NotebookEdit"}


def _in_git_repo(path: str) -> bool:
    parent = os.path.dirname(path) or "."
    if not os.path.isdir(parent):
        return False
    try:
        return subprocess.run(
            ["git", "-C", parent, "rev-parse", "--show-toplevel"],
            capture_output=True, timeout=2,
        ).returncode == 0
    except Exception:
        return False  # unknown -> not artefact evidence; the other predicates still gate


def _parse(transcript: str) -> tuple[list[Turn], list[Touch], list[str]]:
    turns: list[Turn] = []
    touches: list[Touch] = []
    commands: list[str] = []
    try:
        lines = Path(transcript).read_text(errors="replace").splitlines()[-400:]
    except Exception:
        return turns, touches, commands
    for line in lines:
        try:
            rec = json.loads(line)
        except Exception:
            continue
        msg = rec.get("message") or {}
        role = msg.get("role") or rec.get("role") or ""
        content = msg.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text += block.get("text", "")
                elif block.get("type") == "tool_use":
                    p = (block.get("input") or {}).get("file_path")
                    if block.get("name") in _PATH_TOOLS and p:
                        touches.append(Touch(p, _in_git_repo(p)))
        if role in ("user", "assistant") and text.strip():
            turns.append(Turn(role, text))
            commands.extend(re.findall(r"(?:^|\s)(/[a-z][a-z0-9-]{2,})", text))
    return turns[-_WINDOW_TURNS:], touches[-40:], commands


def _turns_since_last(session_id: str, n_assistant: int) -> int:
    try:
        st = json.loads(_STATE.read_text())
        if st.get("session_id") == session_id:
            return n_assistant - int(st.get("at_turn", 0))
    except Exception:
        pass
    return 999


def _record(session_id: str, n_assistant: int, verdict) -> None:
    for target, payload in (
        (_STATE, {"session_id": session_id, "at_turn": n_assistant}),
        (_AUDIT, {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "session_id": session_id, "signals": verdict.signals}),
    ):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target is _STATE:
                target.write_text(json.dumps(payload))
            else:
                with target.open("a") as fh:
                    fh.write(json.dumps(payload) + "\n")
        except Exception:
            pass


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    turns, touches, commands = _parse(payload.get("transcript_path", ""))
    if len(turns) < 4:
        return 0
    n_assistant = sum(1 for t in turns if t.role == "assistant")
    session_id = payload.get("session_id", "")
    verdict = assess(Window(
        turns=tuple(turns), touches=tuple(touches), commands=tuple(commands),
        turns_since_last_advisory=_turns_since_last(session_id, n_assistant),
    ))
    if not verdict.fires:
        return 0
    _record(session_id, n_assistant, verdict)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": render_advisory(verdict),
    }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # fail-open: a hook defect must never disturb a prompt
        sys.exit(0)
