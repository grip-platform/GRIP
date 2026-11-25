#!/usr/bin/env python3
"""PreToolUse on Agent|Task: carry a subagent the GRIP rules it cannot receive natively.

KERN v2 phase 2, the operator's rulings 3, 4 and 5 on GRIP#6658 (2026-10-18).
lib/dispatch_brief.py composes the brief; this hook appends it to the launch's
prompt through hookSpecificOutput.updatedInput.

The shape of the reply is load-bearing, and each part was measured
(docs/measurements/2026-10-06-kern-v2-vendor-probes.md):

- updatedInput replaces the WHOLE tool input, so every field is copied and only
  the prompt is extended.
- No permissionDecision. updatedInput is honoured on its own, and "allow" would
  approve every subagent launch without asking.
- This must stay the ONLY hook that writes updatedInput on Agent or Task: with
  two writers on one launch, neither rewrite survived. Ruling 3;
  tests/test_single_updated_input_writer.py holds it over every bound hook.

An empty brief prints nothing. Every fault exits 0 with nothing printed, so the
hook can never stop a launch. A fault is written to the audit log, because a
failed hook delivers no rules and that has to be visible somewhere.

A launch's rules count as delivered only once the launch has run (ruling 5).
This PreToolUse run holds them under the launch's tool_use_id; --commit, bound
on PostToolUse for Agent|Task, which fires only when the tool ran, moves them
into the type's record. A launch another PreToolUse hook refuses never runs,
so its retry gets the rules in full again (Codex on GRIP#8531).

--renew, bound on PreCompact, forgets this session's deliveries, so each rule
is delivered in full again after a compaction (ruling 5).

Audit: logs/dispatch-brief-hook.jsonl. One line per launch, per committed
launch and per renewal: the recipient type and the rule names delivered in
full, indexed or named, and the brief's size. Never the prompt or the
description.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

TOOLS = frozenset({"Agent", "Task"})
AUDIT_REL = Path("logs") / "dispatch-brief-hook.jsonl"


def _audit(root: Path, record: dict) -> None:
    try:
        path = root / AUDIT_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": stamp, **record}) + "\n")
    except Exception:  # noqa: BLE001 -- an audit failure must never reach the launch
        pass


def _deliver(payload: dict, root: Path) -> None:
    """Print the rewritten launch when there is a brief, then hold what it gave until
    the launch is known to have run."""
    import dispatch_brief as db

    tool_input = payload.get("tool_input")
    if payload.get("tool_name") not in TOOLS or not isinstance(tool_input, dict):
        return
    store = db.SeenStore(db.session_file(root, payload.get("session_id")))
    brief = db.compose(tool_input, root, store)
    updated = db.extended_prompt(tool_input, brief)
    if updated is not None:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "updatedInput": updated}}), flush=True)
        store.pend(payload.get("tool_use_id"), brief.recipient, brief.delivered)
    _audit(root, {"event": "brief", "session": payload.get("session_id"),
                  "launch": payload.get("tool_use_id"),
                  "dispatcher": payload.get("agent_type") or "main",
                  "recipient": brief.recipient, "printed": updated is not None,
                  "full": brief.full, "indexed": brief.indexed, "named": brief.named,
                  "chars": brief.size, "over_cap": brief.over_cap})


def _commit(payload: dict, root: Path) -> None:
    """PostToolUse: the launch ran, so the rules it was given now count as delivered."""
    import dispatch_brief as db

    if payload.get("tool_name") not in TOOLS:
        return
    store = db.SeenStore(db.session_file(root, payload.get("session_id")))
    if store.commit(payload.get("tool_use_id")):
        _audit(root, {"event": "commit", "session": payload.get("session_id"),
                      "launch": payload.get("tool_use_id")})


def _renew(payload: dict, root: Path) -> None:
    import dispatch_brief as db

    cleared = db.SeenStore(db.session_file(root, payload.get("session_id"))).clear()
    _audit(root, {"event": "renew", "session": payload.get("session_id"),
                  "trigger": payload.get("trigger"), "cleared": cleared})


MODES = {"--renew": _renew, "--commit": _commit}


def main(argv: list[str]) -> int:
    root = None
    try:
        from claude_dir import get_claude_dir

        root = get_claude_dir()
        payload = json.load(sys.stdin)
        mode = next((MODES[arg] for arg in argv if arg in MODES), _deliver)
        if isinstance(payload, dict):
            mode(payload, root)
    except Exception as exc:  # noqa: BLE001 -- a fault must never stop a launch
        if root is not None:
            _audit(root, {"event": "error", "error": f"{type(exc).__name__}: {exc}"[:300]})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
