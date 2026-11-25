#!/usr/bin/env python3
"""PreToolUse (Edit|Write) advisory: a session working outside the shared
tree is writing into it.

rules/shared-tree-is-not-canonical.md, mandate 3: feature work never lands in
the shared tree (~/.claude, a symlink onto ~/.grip, which launchd agents
execute from); it belongs in a worktree. lib/shared_tree_write_advisor.py
decides. This file only reads the call, audits a fire and speaks.

ADVISORY ONLY. It never denies and takes no permission decision. It emits
additionalContext for the session and a one-line systemMessage for the
operator through lib/hook_response.pretool_context, so the normal permission
flow applies to the write unchanged. There is no deny path
(rules/gate-resolution-escape-hatch.md, Invariant 1), and
tests/test_shared_tree_write_advisory_hook.py asserts that on this file's
source.

Fail-open: any error prints {} and exits 0, so the advisory can never break a
write. Each fire appends one line to
<shared tree>/logs/shared-tree-write-advisory.jsonl.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _directory in (_HERE, _HERE.parent / "lib"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

AUDIT_RELPATH = Path("logs") / "shared-tree-write-advisory.jsonl"


def _audit(shared: str, record: dict) -> None:
    """Append one fire to the audit log. A failed append is not an error."""
    try:
        path = Path(shared) / AUDIT_RELPATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        pass


def run(hook_input: object) -> dict:
    """The response to one parsed hook input."""
    # Imported here rather than at module level so that a broken lib/ is
    # caught by the fail-open wrapper below instead of killing the hook.
    from hook_response import pretool_context, silent_allow
    from hook_utils import extract_tool_info
    from shared_tree_write_advisor import advise, shared_tree_root

    if not isinstance(hook_input, dict):
        return silent_allow()
    tool_name, tool_input, _ = extract_tool_info(hook_input)
    target = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    cwd = hook_input.get("cwd") or os.getcwd()
    shared = shared_tree_root()
    message = advise(target, cwd, shared) if shared else None
    if not message:
        return silent_allow()
    _audit(shared, {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": hook_input.get("session_id", ""),
        "tool": tool_name,
        "file_path": target,
        "cwd": cwd,
        "shared_tree": shared,
    })
    return pretool_context(message, system_message=message.splitlines()[0])


def main() -> None:
    from hook_response import emit
    from hook_utils import parse_hook_input

    emit(run(parse_hook_input()))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # fail-open: an advisory must never break a write
        print("{}")
