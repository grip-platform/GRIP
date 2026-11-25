#!/usr/bin/env python3
"""PreToolUse (Write) advisory: copy a Claude Code plan aside before a Write
replaces it.

Claude Code reuses one plan file per session and overwrites it when the task
changes. lib/plan_archive.py copies the old plan to <plans>/archive/ first.
This file only reads the call, logs a copy and tells the operator where the
old plan went.

ADVISORY ONLY. It never refuses a Write and takes no permission decision, so
the harness's own permission flow applies to the Write unchanged. There is no
refusal path (rules/gate-resolution-escape-hatch.md, Invariant 1), and
tests/test_plan_archive.py asserts that on this file's source.

Fail-open: any error prints {} and exits 0, so a failed copy can never stop a
plan from being written. Each copy, and each failed copy with its error,
appends one line to <GRIP>/logs/plan-archive-hook.jsonl, or to
GRIP_PLAN_ARCHIVE_LOG when set.
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

DEFAULT_LOG = _HERE.parent / "logs" / "plan-archive-hook.jsonl"


def _audit(log: Path, record: dict) -> None:
    """Append one copy to the audit log. A failed append is not an error."""
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        pass


def run(hook_input: object, log: Path = DEFAULT_LOG) -> dict:
    """The response to one parsed hook input."""
    # Imported here rather than at module level so that a broken lib/ is
    # caught by the fail-open wrapper below instead of killing the hook.
    from hook_response import pretool_context, silent_allow
    from plan_archive import archive_before_overwrite

    if not isinstance(hook_input, dict) or hook_input.get("tool_name") != "Write":
        return silent_allow()
    tool_input = hook_input.get("tool_input")
    if not isinstance(tool_input, dict) or not tool_input.get("file_path"):
        return silent_allow()
    content = tool_input.get("content")
    kept = archive_before_overwrite(
        tool_input["file_path"], content if isinstance(content, str) else ""
    )
    if kept is None:
        return silent_allow()
    _audit(log, {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": hook_input.get("session_id", ""),
        "plan": str(tool_input["file_path"]),
        "archived_to": str(kept),
    })
    note = f"The plan being replaced was archived first, to {kept}."
    return pretool_context(note, system_message=note)


def main() -> None:
    from hook_response import emit
    from hook_utils import parse_hook_input

    log = Path(os.environ.get("GRIP_PLAN_ARCHIVE_LOG") or DEFAULT_LOG)
    try:
        response = run(parse_hook_input(), log)
    except Exception as exc:  # fail-open, but the failed copy is logged, not silent
        _audit(log, {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error": f"{type(exc).__name__}: {exc}",
        })
        response = {}
    emit(response)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # fail-open: an archive must never stop a plan being written
        print("{}")
