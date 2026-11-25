#!/usr/bin/env python3
"""
PreToolUse hook: Closed-Loop Guard for operational references.

Fires before every Slack tool call. Checks operational-context.md for known
channel IDs before any live API lookup is attempted. Advisory only — never blocks.

Principle source: ~/.claude/CLAUDE.md ## Closed-Loop Memory (Permanent)
Tested by:        ~/.claude/tests/test_closed_loop_guard.py::TestHookRegistration
Library:          ~/.claude/lib/closed_loop_guard.py

Audit log: ~/.claude/learning/closed-loop-audit.jsonl
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_CLAUDE_DIR = Path.home() / ".claude"

# Slack ID prefixes — already resolved, no lookup needed
_SLACK_ID_PREFIXES = ("C", "D", "G", "W")


def _parse_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _is_slack_tool(tool_name: str) -> bool:
    return "slack" in tool_name.lower()


def _extract_channel_query(tool_input: dict) -> str | None:
    for key in ("channel_id", "channel", "query", "name"):
        val = tool_input.get(key)
        if val and isinstance(val, str):
            return val
    return None


def _is_already_resolved(value: str) -> bool:
    """Return True if value looks like a canonical Slack ID, not a name."""
    return (
        len(value) >= 6
        and value[0].upper() in _SLACK_ID_PREFIXES
        and value[1:].replace("_", "").isalnum()
    )


def main() -> None:
    payload = _parse_stdin()
    tool_name = payload.get("tool_name", "")

    if not _is_slack_tool(tool_name):
        sys.exit(0)

    tool_input = payload.get("tool_input", {})
    channel_query = _extract_channel_query(tool_input)

    if not channel_query or _is_already_resolved(channel_query):
        sys.exit(0)

    try:
        if str(_CLAUDE_DIR) not in sys.path:
            sys.path.insert(0, str(_CLAUDE_DIR))
        from lib.closed_loop_guard import ClosedLoopGuard

        guard = ClosedLoopGuard()
        query_type = "dm" if "dm" in tool_name.lower() else "slack_channel"
        known = guard.check_before_lookup(query_type, channel_query)

        if known:
            print(
                f"[closed-loop-guard] Tier 0 HIT: {channel_query!r} → {known}"
                f" — in operational-context.md, no API lookup needed",
                file=sys.stderr,
            )
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
