#!/usr/bin/env python3
"""HITL Quality Gate — PreToolUse hook for AskUserQuestion.

Validates AskUserQuestion payloads against the universal quality gates
documented in skills/hitl/SKILL.md before the tool call reaches the UI.
Blocks malformed questions at tool-call time rather than allowing them
to ship to the operator.

Enforced gates (from lib/hitl_quality_gates.py):
- Header <= 12 chars
- 2-4 options per question
- 1-4 questions per batched call
- Question ends with '?'
- No capital-O 'Other' mention (AskUserQuestion UI injects it)
- Option shape: dict with non-empty label + description

Fail-open policy: any error importing the validator, parsing the hook
input, or reading the payload returns allow. This hook MUST NOT produce
false positives that block well-formed questions.

Registered under PreToolUse with matcher "AskUserQuestion" in
~/.claude/settings.json. `settings.json` is per-operator local config
(gitignored), so new operators must add the following snippet to their
local settings.json under the existing `hooks.PreToolUse` array:

    {
      "matcher": "AskUserQuestion",
      "hooks": [
        {
          "type": "command",
          "command": "bash $HOME/.claude/lib/grip-python.sh $HOME/.claude/hooks/hitl-quality-hook.py",
          "timeout": 5000,
          "statusMessage": "HITL quality gate check"
        }
      ]
    }

Or via the `update-config` skill: "add hitl-quality-hook.py as a
PreToolUse hook matching AskUserQuestion".
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

# Ensure lib/ is importable
_CLAUDE_DIR = os.path.join(os.environ.get("HOME", ""), ".claude")
if _CLAUDE_DIR not in sys.path:
    sys.path.insert(0, _CLAUDE_DIR)


MAX_VIOLATIONS_IN_REASON = 5


def _allow_response() -> dict:
    """The schema-valid silent allow (see lib/hook_response.py)."""
    return {}


def _deny_response(violations: list[str]) -> dict:
    """Compose a deny response that names each violation and points at the fix.

    Emits the modern hookSpecificOutput.permissionDecision shape — the old
    top-level {"decision": "deny"} was invalid vocabulary and made this gate
    silently non-enforcing (see lib/hook_response.py).
    """
    from lib.hook_response import pretool_decision
    header = (
        "[HITL QUALITY GATE] AskUserQuestion payload violates the universal "
        "HITL quality gates documented in skills/hitl/SKILL.md:"
    )
    shown = violations[:MAX_VIOLATIONS_IN_REASON]
    body = "\n".join(f"  - {v}" for v in shown)
    overflow = ""
    if len(violations) > MAX_VIOLATIONS_IN_REASON:
        overflow = f"\n  (+{len(violations) - MAX_VIOLATIONS_IN_REASON} more)"
    fix = (
        "\nFix: headers <= 12 chars, 2-4 options per question, "
        "question text ends with '?', no capital-O 'Other' mention, "
        "options are dicts with non-empty label + description. "
        "Route multi-question flows through /hitl for consistent dispatch."
    )
    return pretool_decision("deny", header + "\n" + body + overflow + fix)


def evaluate_payload(event: Any) -> dict[str, str]:
    """Evaluate a hook event dict and return an allow or deny response.

    Extracted as a pure function so unit tests can exercise it without
    subprocess/stdin round-trips.
    """
    if not isinstance(event, dict):
        return _allow_response()

    if event.get("tool_name", "") != "AskUserQuestion":
        return _allow_response()

    tool_input = event.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return _allow_response()

    questions = tool_input.get("questions")
    if questions is None:
        # Missing questions key — let the tool itself surface the schema error
        return _allow_response()

    try:
        from lib.hitl_quality_gates import collect_violations
    except Exception:
        # Validator module missing or broken — fail open
        return _allow_response()

    try:
        violations = collect_violations(questions)
    except Exception:
        # Validator crash on unexpected input shape — fail open
        return _allow_response()

    if not violations:
        return _allow_response()

    return _deny_response(violations)


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except Exception:
        print(json.dumps(_allow_response()))
        return

    response = evaluate_payload(event)
    print(json.dumps(response))


if __name__ == "__main__":
    main()
