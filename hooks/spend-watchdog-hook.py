#!/usr/bin/env python3
"""UserPromptSubmit hook: one advisory line when a Workflow run in this
session has stalled, or this session is burning tokens fast.

Origin: rules/fan-out-spends-the-weekly-limit.md. Advisory only -- this
hook has no deny path at all. It prints at most one line and always exits
0, whether or not it found anything, because it can only be ignored, never
block. Mirrors hooks/time-awareness-hook.py for the UserPromptSubmit
response shape (additionalContext for Claude, systemMessage for the
operator); lib.hook_response's builders are PreToolUse-shaped and are not
reused here for that reason.

Detector: lib/spend_watchdog.py
Tests:    tests/test_spend_watchdog.py
Rule:     rules/fan-out-spends-the-weekly-limit.md
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_CLAUDE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_CLAUDE_DIR))

from lib.claude_dir import get_claude_dir  # noqa: E402
from lib.path_encoding import encode_project_path  # noqa: E402
from lib.spend_watchdog import assess, render_line, resolve_session_id  # noqa: E402

# Respects a HAL_HOME override (Codex review, GRIP#8698): see the matching
# comment in hooks/workflow-budget-gate.py.
_LEDGER = Path(os.environ.get("HAL_HOME") or Path.home() / ".hal") / "logs" / "cache-loss-ledger.jsonl"


def _read_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        parsed = json.loads(raw) if raw.strip() else {}
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def main() -> int:
    try:
        payload = _read_stdin()
        projects_dir = get_claude_dir() / "projects" / encode_project_path(str(Path.cwd()))
        session_id = resolve_session_id(payload, dict(os.environ), projects_dir)
        if not session_id:
            return 0
        result = assess(projects_dir / session_id, _LEDGER, session_id)
        line = render_line(result)
        if line:
            print(json.dumps({
                "hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                        "additionalContext": line},
                "systemMessage": line,
            }))
    except Exception:
        pass  # advisory only: never let a bug here touch the prompt
    return 0


if __name__ == "__main__":
    sys.exit(main())
