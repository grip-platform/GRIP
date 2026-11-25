#!/usr/bin/env python3
"""PreToolUse hook: gate a Workflow-tool launch on declared cost.

Origin: rules/fan-out-spends-the-weekly-limit.md -- a single Workflow run
spawned 273 default-type agents across 11 depths to converge one reply,
moving the weekly Claude limit 0.04 -> 0.36 (later 0.51) and stalling four
and a half hours at depth 8. Mirrors hooks/guardrail-evasion-gate.py: stdin
JSON in, lib.hook_response.emit / pretool_decision out, same lib path
bootstrap. Deliberately NOT a record-signing module, so it does not call
lib.pq_reexec -- it only ever appends a plain JSONL audit line, never a
signed record, and it is invoked through grip-python.sh in settings.json
per rules/never-system-python-for-grip.md rather than reexec-ing itself.

Escape-hatch audit (rules/gate-resolution-escape-hatch.md, Invariant 1): the
only thing this hook can deny is one Workflow launch. Its resolution --
editing the script to add or lower the agent cap, switch to a lightweight
agent type, add a reasoned override comment, or simply wait for the weekly
or five-hour window to reset -- is never itself a Workflow launch, so this
gate cannot block its own resolution. Classified NARROW_GATES + registered
in BLOCK_FAILURE_ESCAPE_ROUTES (tests/test_gate_escape_hatch.py): settings.json
binds this hook with onFailure "block", so a CRASHED hook still blocks the
one Workflow launch it was judging, and the escape route is Bash/Edit --
outside this binding's Workflow-only matcher -- repairing the gate or the
script never needs the hook to run.

Detector: lib/workflow_budget_gate.py
Tests:    tests/test_workflow_budget_gate.py
Audit:    ~/.claude/logs/workflow-budget-gate.jsonl
Rule:     rules/fan-out-spends-the-weekly-limit.md
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_CLAUDE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_CLAUDE_DIR))

from lib.hook_response import emit, pretool_context, pretool_decision  # noqa: E402
from lib.workflow_budget_gate import decide, latest_utilisation  # noqa: E402

_AUDIT_LOG = _CLAUDE_DIR / "logs" / "workflow-budget-gate.jsonl"
# Respects a HAL_HOME override (Codex review, GRIP#8698): a hardcoded ~/.hal
# silently reads a missing or unrelated ledger on any install that sets it,
# so latest_utilisation() returns None and a launch is allowed even when the
# ACTUAL active account is already past a ceiling.
_HAL_HOME = Path(os.environ.get("HAL_HOME") or Path.home() / ".hal")
_LEDGER = _HAL_HOME / "logs" / "cache-loss-ledger.jsonl"
_GATED_TOOL = "Workflow"


def _read_stdin() -> dict:
    """The hook's JSON payload, or {} for anything that is not a JSON object.

    Valid JSON that parses to a list/string/number (``[]``, not malformed
    text) used to reach ``payload.get(...)`` below and crash with an
    AttributeError -- uncaught, which under this binding's onFailure
    "block" denied the launch with no audit line at all (Codex review,
    GRIP#8698, reproduced: ``echo '[]' | python3 hooks/workflow-budget-gate.py``).
    Treating a non-dict result the same as empty input keeps this on the
    module's own stated fail-open contract instead of crashing into the
    harness's fail-closed one.
    """
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _audit(record: dict) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with _AUDIT_LOG.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def main() -> int:
    payload = _read_stdin()
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    if tool_name != _GATED_TOOL:
        return 0
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    session_id = payload.get("session_id")

    utilisation = latest_utilisation(_LEDGER, session_id=session_id)
    verdict = decide(tool_input, utilisation, dict(os.environ))

    _audit({
        "decision": verdict["decision"],
        "reasons": verdict["reasons"],
        "estimate": verdict.get("estimate"),
        "utilisation": utilisation,
        "cap": verdict.get("cap"),
    })

    reason = "; ".join(verdict["reasons"])
    if verdict["decision"] == "deny":
        emit(pretool_decision("deny", reason or "workflow-budget-gate: refused"))
    elif verdict["decision"] == "warn":
        print(f"[WORKFLOW BUDGET] {reason}", file=sys.stderr)
        emit(pretool_context(reason, system_message=f"[WORKFLOW BUDGET] {reason}"))
    # allow -> no output at all, same as guardrail-evasion-gate.py's allow path
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
