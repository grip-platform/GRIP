#!/usr/bin/env python3
"""PreToolUse (Bash): deny a publish whose test count no run in this session reported.

Reads the event on stdin, hands the command, the transcript path and the
payload's ``agent_id`` to ``lib.measured_claim_gate.decide`` (a dispatched
worker is checked against its own transcript, GRIP#8331) and answers through
``lib.hook_response``: ``deny`` with the reason, ``additionalContext`` for a
warn, nothing for an allow. An override and every non-allow decision are
logged to ``logs/measured-claim-gate.jsonl`` under CLAUDE_DIR, with the
``agent_id`` and the transcript the gate read. The gate fails open on its own
errors (``rules/measured-claims-read-the-output-first.md``).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.hook_response import emit, pretool_context, pretool_decision  # noqa: E402
from lib.measured_claim_gate import decide  # noqa: E402


def _log(entry: dict) -> None:
    try:
        path = (Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
                / "logs" / "measured-claim-gate.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": round(time.time(), 3), **entry}) + "\n")
    except OSError:
        pass


def _read_event():
    """The PreToolUse event as a dict, or None when stdin is not one."""
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        return None
    return event if isinstance(event, dict) else None


def main() -> None:
    event = _read_event()
    if event is None or event.get("tool_name") != "Bash":
        return
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else ""
    agent_id = event.get("agent_id")
    agent_id = agent_id if isinstance(agent_id, str) else ""
    try:
        decision = decide(command or "", event.get("transcript_path"), os.environ,
                          event.get("cwd"), agent_id=agent_id)
    except Exception as exc:  # noqa: BLE001 -- a publish gate fails open, and says so in its log
        _log({"action": "error", "error": repr(exc), "agent_id": agent_id})
        return
    if decision.action == "allow" and "overridden" not in decision.reason:
        return
    _log({"action": decision.action, "reason": decision.reason,
          "claims": list(decision.claims), "observed": list(decision.observed),
          "agent_id": agent_id, "transcript": decision.transcript,
          "transcript_file": decision.transcript_file, "command": (command or "")[:400]})
    if decision.action == "deny":
        emit(pretool_decision("deny", decision.reason))
    elif decision.action == "warn":
        emit(pretool_context("[GRIP measured-claim gate] " + decision.reason))


if __name__ == "__main__":
    main()
