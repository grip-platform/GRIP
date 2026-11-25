#!/usr/bin/env python3
"""PreToolUse advisory gate: demand a controlled repro before premise-driven dispatches.

Fires on Agent/Task dispatches. When the dispatch prompt ASSERTS a failure
premise ("X is broken", "Y is missing", "Z corrupted my data") with no sign of
a controlled reproduce-test (a command + observed output block, "reproduced:",
a controlled test, exit codes, file:line citations), it injects an advisory
WARN as additional context. It ALWAYS allows — v1 is advisory, never a deny —
and fails open on any internal error (a broken safety hook must never block
work).

Detector: lib/premise_reproduce_gate.py (pure, zero side effects)
Tests:    tests/test_premise_reproduce_gate.py
Audit:    logs/premise-reproduce-gate.jsonl (one line per premise-bearing dispatch)
Origin:   2026-07-14 RTK incident (hypothesis H798) — an "rg -rn" typo mangled
          reads, the mangling was blamed on RTK, and a two-session false
          investigation followed because nobody ran the <30s ground-truth test
          that would have refuted the premise immediately.

Activation is the operator-gated step (wire into settings.json PreToolUse for
Agent/Task). Until wired, this file is inert — zero blast radius from merging.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_CLAUDE = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))
if str(_CLAUDE) not in sys.path:
    sys.path.insert(0, str(_CLAUDE))

try:
    from lib.premise_reproduce_gate import classify_dispatch_prompt
except Exception:  # noqa: BLE001 — fail-open: a broken import must never block a dispatch
    sys.exit(0)

_DISPATCH_TOOLS = {"Agent", "Task"}
_AUDIT = _CLAUDE / "logs" / "premise-reproduce-gate.jsonl"

_WARN_TEMPLATE = (
    "[PREMISE-REPRODUCE] failure-premise without a controlled repro — run the "
    "<30s ground-truth test first (rules: verify the premise, not the "
    "conclusion). {reason}"
)


def _audit(record: dict) -> None:
    """Append one JSONL audit record; audit loss never blocks the dispatch."""
    try:
        _AUDIT.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with _AUDIT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"[premise-reproduce-gate] audit write failed ({exc})", file=sys.stderr)


def _emit_warn(reason: str) -> None:
    """Inject the advisory as PreToolUse additional context (never a deny)."""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": _WARN_TEMPLATE.format(reason=reason),
    }}))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — fail-open on malformed stdin
        return 0
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if tool not in _DISPATCH_TOOLS:
        return 0
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    prompt = tool_input.get("prompt") or ""
    if not prompt:
        return 0

    verdict = classify_dispatch_prompt(prompt)
    if not verdict["premise_detected"]:
        return 0
    _audit({"tool": tool, "verdict": verdict["verdict"],
            "premises": verdict["premises"], "evidence": verdict["evidence"]})
    if verdict["verdict"] == "warn":
        _emit_warn(verdict["reason"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — fail-open: never block work on a hook defect
        sys.exit(0)
