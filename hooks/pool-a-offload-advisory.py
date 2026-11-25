#!/usr/bin/env python3
"""PreToolUse (Agent|Task, Bash): advise a Pro-tier session that a spawn it is
about to make could run on HAL's Pool B instead, for free.

lib.pool_a_offload_advisor.advise decides whether to speak and what; this
file is the impure half: it reads the event, resolves the tier envelope,
keeps a tiny per-session "already advised" file so each kind
(rules/pool-a-offload-routing.md) fires at most once per session, and emits
additionalContext plus a one-line systemMessage through lib.hook_response.
It never denies and takes no permission decision -- there is no deny path to
escape (rules/gate-resolution-escape-hatch.md, Invariant 1). Every fire is
logged to logs/pool-a-offload-advisory.jsonl under CLAUDE_DIR.

Every import below is lazy, inside main(), so a broken lib/ is caught by the
fail-open wrapper at the bottom instead of killing the hook (the same reason
hooks/shared-tree-write-advisory.py imports lazily). Fails open: any error,
or a malformed payload, prints nothing and exits 0.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _state_path(get_claude_dir, session_id: str) -> Path:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_") or "unknown"
    return get_claude_dir() / "state" / "pool-a-offload-advisory" / f"{safe}.json"


def _already_advised(path: Path) -> set:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")).get("advised", []))
    except (OSError, ValueError):
        return set()


def _mark_advised(path: Path, advised: set) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"advised": sorted(advised)}), encoding="utf-8")
    except OSError:
        pass


def _log(get_claude_dir, entry: dict) -> None:
    try:
        path = get_claude_dir() / "logs" / "pool-a-offload-advisory.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _read_event() -> dict | None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        return None
    return event if isinstance(event, dict) else None


def main() -> None:
    from lib.claude_dir import get_claude_dir
    from lib.grip_tier import envelope
    from lib.hook_response import emit, pretool_context
    from lib.pool_a_offload_advisor import advise

    event = _read_event()
    if event is None:
        return
    tool_name = event.get("tool_name")
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    session_id = str(event.get("session_id") or "")
    state_path = _state_path(get_claude_dir, session_id)
    already = _already_advised(state_path)
    result = advise(tool_name, command, envelope(), already)
    if result is None:
        return
    kind, text = result
    already.add(kind)
    _mark_advised(state_path, already)
    _log(get_claude_dir, {
        "session_id": session_id, "tool": tool_name, "kind": kind,
    })
    emit(pretool_context(text, system_message=text.splitlines()[0]))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # fail-open: an advisory must never break a spawn
        print("{}")
