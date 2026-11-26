#!/usr/bin/env python3
"""PreToolUse (Bash): two independent publish checks, next to each other.

1. Deny a publish whose test count no run in this session reported. Reads
   the event on stdin, hands the command, the transcript path and the
   payload's ``agent_id`` to ``lib.measured_claim_gate.decide`` (a dispatched
   worker is checked against its own transcript, GRIP#8331) and answers
   through ``lib.hook_response``: ``deny`` with the reason,
   ``additionalContext`` for a warn, nothing for an allow. An override and
   every non-allow decision are logged to ``logs/measured-claim-gate.jsonl``
   under CLAUDE_DIR, with the ``agent_id`` and the transcript the gate read.

2. Unless (1) above already denies: deny a publish that reads a file whose
   refused Edit, Write or MultiEdit is still missing from it (GRIP#9394),
   through ``lib.stale_post_gate.decide``. Logged the same way, to
   ``logs/stale-post-gate.jsonl``. Precedence is DENY (either check) over
   WARN (from (1) only) over silent allow -- a WARN never disarms this
   check, because the harness reads at most one verdict per invocation, so
   (1)'s WARN is held back and emitted only once (2) has had its own say.

Each check fails open on its own errors and never on the other's
(``rules/measured-claims-read-the-output-first.md``).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# lib/ itself, not just the repo root: lib.stale_post_gate and the sibling
# modules it imports (measured_claim_gate, transcript_text, untrusted_turn)
# use bare imports of each other, matching lib.untrusted_turn's own existing
# convention, and hooks/untrusted-egress-gate.py already adds this exact
# path for that reason. This does load measured_claim_gate.py a second time
# under its bare name, alongside the lib.measured_claim_gate import below --
# accepted here, as it is there: the module holds only regex constants and
# pure functions, no mutable state to duplicate (AGORA on GRIP#9394).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from lib.hook_response import emit, pretool_context, pretool_decision  # noqa: E402
from lib.measured_claim_gate import decide  # noqa: E402
from lib.stale_post_gate import decide as decide_stale_post  # noqa: E402


def _log(name: str, entry: dict) -> None:
    try:
        path = (Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
                / "logs" / f"{name}.jsonl")
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


def _measured_claim_decision(command: str, event: dict, agent_id: str):
    """The measured-claim gate's own verdict, logged; None on its own error (logged too).

    A bare allow (no override) is never logged, matching the gate's own contract
    that a routine pass leaves no trace.
    """
    try:
        decision = decide(command, event.get("transcript_path"), os.environ,
                          event.get("cwd"), agent_id=agent_id)
    except Exception as exc:  # noqa: BLE001 -- a publish gate fails open, and says so in its log
        _log("measured-claim-gate", {"action": "error", "error": repr(exc), "agent_id": agent_id})
        return None
    if decision.action == "allow" and "overridden" not in decision.reason:
        return decision
    _log("measured-claim-gate",
        {"action": decision.action, "reason": decision.reason,
         "claims": list(decision.claims), "observed": list(decision.observed),
         "agent_id": agent_id, "transcript": decision.transcript,
         "transcript_file": decision.transcript_file, "command": command[:400]})
    return decision


def _stale_post_decision(command: str, event: dict, agent_id: str):
    """The stale-post gate's own verdict (GRIP#9394), logged; None on its own error (logged too)."""
    try:
        decision = decide_stale_post(command, event.get("transcript_path"), os.environ,
                                     event.get("cwd"), agent_id=agent_id)
    except Exception as exc:  # noqa: BLE001 -- fails open, like the gate above
        _log("stale-post-gate", {"action": "error", "error": repr(exc), "agent_id": agent_id})
        return None
    if decision.action == "allow" and "overridden" not in decision.reason:
        return decision
    _log("stale-post-gate",
        {"action": decision.action, "reason": decision.reason, "path": decision.path,
         "agent_id": agent_id, "transcript_file": decision.transcript_file, "command": command[:400]})
    return decision


def main() -> None:
    """Precedence across the two checks: DENY from either wins; a WARN from the
    measured-claim gate is emitted only once the stale-post gate has also had its
    say, so it can never silently disarm the other's deny (AGORA on GRIP#9394
    round 1 -- an advisory WARN used to count as "already answered" and stopped
    the stale-post check from running at all)."""
    event = _read_event()
    if event is None or event.get("tool_name") != "Bash":
        return
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else ""
    command = command or ""
    agent_id = event.get("agent_id")
    agent_id = agent_id if isinstance(agent_id, str) else ""

    claim = _measured_claim_decision(command, event, agent_id)
    if claim is not None and claim.action == "deny":
        emit(pretool_decision("deny", claim.reason))
        return

    stale = _stale_post_decision(command, event, agent_id)
    if stale is not None and stale.action == "deny":
        emit(pretool_decision("deny", stale.reason))
        return

    if claim is not None and claim.action == "warn":
        emit(pretool_context("[GRIP measured-claim gate] " + claim.reason))


if __name__ == "__main__":
    main()
