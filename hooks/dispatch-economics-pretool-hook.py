#!/usr/bin/env python3
"""PreToolUse hook: dispatch-economics capability WARN (C2, week-one).

Fires on Agent / Task dispatch. Runs the pure capability classifier
(lib/dispatch_kernel). WARN-ONLY per V>> HITL Q1 (2026-05-15): it NEVER
blocks this week — a false positive is an ignorable advisory line, not a
session-bricking stall (devil council objection 2 defanged by construction).
Records a shadow-ledger row (lib/dispatch_telemetry) so H490 / H-DISP-4 can
measure WARN precision over time.

Escalation to a hard DENY is conditional and deliberately NOT implemented
here: only after the H488 / H-DISP-2 real-log replay clears >=90% precision
AND <2% false-positive AND a GRIP_DISPATCH_BYPASS one-shot exists (V>> HITL
Q2 severity ladder). Until then this hook is purely advisory.

Fail-open by construction: any parse/import/logic error exits 0 with an
empty decision. An advisory hook must never break a dispatch.

Doctrine: rules/dispatch-economics.md. Plan:
plans/dispatch-kernel-mechanical-enforcement.md.
"""

from __future__ import annotations

import json
import os
import sys

_DISPATCH_TOOLS = frozenset({"Agent", "Task"})


def _parse_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _prompt_of(tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("prompt") or tool_input.get("description") or "")


def main() -> int:
    # The entire body is fail-open: an advisory dispatch hook must never be
    # the reason a dispatch breaks. This is the one legitimate place for a
    # broad guard — a system boundary whose contract is "never obstruct".
    try:
        data = _parse_stdin()
        if data.get("tool_name") not in _DISPATCH_TOOLS:
            print("{}")
            return 0

        sys.path.insert(0, os.path.join(os.environ.get("HOME", "/tmp"), ".claude"))
        from lib.dispatch_kernel import capability_verdict
        from lib.dispatch_telemetry import record_suppression

        prompt = _prompt_of(data.get("tool_input", {}))
        verdict = capability_verdict(prompt)

        if verdict.undelegatable:
            sys.stderr.write(
                f"[DISPATCH_CAPABILITY_WARN:{verdict.confidence}] "
                f"{verdict.reason}. {verdict.advice}\n"
            )
            record_suppression(
                prompt_shape=verdict.reason,
                est_useful_work=len(prompt),
                discouraged_parallel_count=1,
                proceeded_anyway=True,
                produced_deliverable=False,
            )
    except Exception:
        pass

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
