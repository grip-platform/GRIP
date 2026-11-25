#!/usr/bin/env python3
"""PreToolUse (Bash) and PreCompact: teach a command-moment rule once, at its first command.

PreToolUse: the first command in a recipient context (the session, or one
subagent) that triggers a command-moment rule is refused once, and the refusal
reason carries the rule's full text; the command, run again, passes, as does
every later command that triggers the same version of that rule. With --renew,
bound on PreCompact, it forgets what the session was taught, so every rule is
taught again after compaction. The mechanism, its triggers, its record and its
escape-hatch audit are in lib/teach_once.py.

Every decision about a matched command (teach, pass), every renewal and every
fail-open path (open, error) is appended to logs/teach-once-gate.jsonl under
CLAUDE_DIR, without the command's text. A command that triggers no rule is not
logged. Fails open: any error lets the command run.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from lib import teach_once
    from lib.claude_dir import get_claude_dir
    from lib.hook_response import emit, pretool_decision
except Exception:  # noqa: BLE001 -- a broken import must never block a command
    sys.exit(0)

_LOG = Path("logs") / "teach-once-gate.jsonl"


def _log(root: Path, entry: dict) -> None:
    try:
        path = root / _LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": round(time.time(), 3), **entry}) + "\n")
    except OSError:
        pass


def _read_event():
    """The hook payload as a dict, or None when stdin is not one."""
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        return None
    return event if isinstance(event, dict) else None


def _session(event: dict) -> str:
    session_id = event.get("session_id")
    return session_id if isinstance(session_id, str) else ""


def _command(event: dict) -> str:
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else ""
    return command if isinstance(command, str) else ""


def _renew(root: Path, event: dict) -> None:
    session_id = _session(event)
    if session_id:
        removed = teach_once.renew(root, session_id)
        _log(root, {"action": "renew", "session": session_id, "removed": removed})


def _teach(root: Path, session_id: str, decision, record: dict) -> None:
    """Write the record first; refuse only once the mark that lets the re-run pass is on disk."""
    versions = {lesson.rule: lesson.version for lesson in decision.lessons}
    try:
        teach_once.mark_taught(root, session_id, decision.context, versions)
    except OSError as exc:
        _log(root, {**record, "action": "open",
                    "note": f"record not written ({type(exc).__name__}), so no refusal"})
        return
    reason = teach_once.refusal_reason(decision.lessons)
    emit(pretool_decision("deny", reason))
    _log(root, {**record, "reason_chars": len(reason)})


def _gate(root: Path, event: dict) -> None:
    if event.get("tool_name") != "Bash":
        return
    session_id = _session(event)
    decision = teach_once.decide(_command(event), session_id, event.get("agent_id"), root)
    if decision.action == "none":
        return
    record = {"session": session_id, **teach_once.audit_record(decision)}
    if decision.action == "teach":
        _teach(root, session_id, decision, record)
    else:
        _log(root, record)


def main(argv: list) -> int:
    event = _read_event()
    if event is None:
        return 0
    root = get_claude_dir()
    try:
        (_renew if "--renew" in argv else _gate)(root, event)
    except Exception as exc:  # noqa: BLE001 -- a teaching gate fails open, and says so in its log
        _log(root, {"action": "error", "session": _session(event), "error": repr(exc)[:300]})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
