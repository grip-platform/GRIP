#!/usr/bin/env python3
"""PreToolUse gate: hold a send that follows raw web text in the same turn (GRIP#8959).

A session that reads a web page holds real tools, and a page can carry text aimed at the
agent. rules/untrusted-content-capability-lock.md puts the defence where the text would
act. This gate watches one place it acts: a Bash command that sends data out after raw
web text entered the turn.

WHAT IT DOES
------------
lib/untrusted_turn.judge reads the pending command (lib/untrusted_egress.py) and the
transcript back to the operator's last message. When the command sends data out and raw
web text entered the turn, GRIP_EGRESS_GATE in the launch environment decides:

* unset or "advise" (the default): one line of context for Claude, and an audit row that
  says whether "hold" would have denied the send;
* "hold": a send to a destination outside the trusted owners is DENIED with a reason that
  names what came in, what would go out and the release; a routine send, such as a git
  push to this checkout's origin, gets the note and goes ahead;
* "off": the audit row only.

Advise is the default until the operator promotes the gate on the audit log's would-be
holds (GRIP#8959's falsification clause, rules/knowledge-maturation-functor.md). A word
written in front of a command does not reach this process, so a page cannot change the
mode.

WHY DENY, AND WHY FAIL OPEN
---------------------------
GRIP sessions run in bypassPermissions mode, where the vendor documents permission
prompts as off, so an "ask" might never be seen; a deny holds in every mode, and a -p run
denies an ask anyway. The gate is bound to Bash, so a crash that blocked would stop every
command: every error of its own lets the command through and writes an audit row.

DEADLOCK CHECK (rules/gate-resolution-escape-hatch.md, Invariant 1)
-------------------------------------------------------------------
Blocked condition: a send outside the trusted owners, in a turn that read raw web text.
Its resolution is the operator's next message, which starts a new turn; no tool call is
needed, so nothing in the denied set can be required. The launch switch is the second
route, and the only one a headless `claude -p` run has: it gets no next message, so a hold
there stops that send for the rest of the run, which is the point. Every other command, a
routine send included, still runs.

Judgement: lib/untrusted_turn.py over lib/untrusted_egress.py
Tests:     tests/test_untrusted_egress.py
Audit:     logs/untrusted-egress-gate.jsonl (kinds, hosts and repositories only)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CHECKOUT / "lib"))


def _audit(record: dict) -> None:
    try:
        from claude_dir import get_claude_dir

        path = get_claude_dir() / "logs" / "untrusted-egress-gate.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except Exception:  # noqa: BLE001 -- auditing must never block a command
        pass


def _payload() -> dict:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 -- an unreadable payload is not this gate's to block
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    payload = _payload()
    if payload.get("tool_name") != "Bash":
        sys.exit(0)
    try:
        import untrusted_turn as turn
        from hook_response import emit, pretool_context, pretool_decision

        verdict = turn.judge(payload)
        if verdict is None:
            sys.exit(0)
        _audit(turn.audit_record(verdict, payload))
        if verdict.decision == turn.HOLD:
            emit(pretool_decision("deny", turn.hold_reason(verdict)))
        elif verdict.decision == turn.NOTE:
            emit(pretool_context(turn.note_text(verdict)))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 -- fail open, and say so in the audit
        # The type only: an exception's message can carry a path or a piece of the command.
        _audit({"decision": "error", "error": type(exc).__name__})
    sys.exit(0)


if __name__ == "__main__":
    main()
