"""Canonical, schema-valid Claude Code hook responses (PreToolUse, plus the PostToolUse advisory).

Origin (2026-07-08, /learn on a live dispatch error): five hooks emitted
``{"decision": "allow"|"deny"}`` — vocabulary the harness never accepted
(the legacy top-level ``decision`` field takes ``approve|block``; the modern
form is ``hookSpecificOutput.permissionDecision`` with ``allow|deny|ask``).
Every such output failed harness validation ("Hook JSON output validation
failed — (root): Invalid input") as a NON-BLOCKING error, so advisory
reasons were silently dropped and — far worse — two DENY gates
(hitl-quality, cross-org Gate R) never actually denied. The invalid
vocabulary had been canonicalised by hooks/CLAUDE.md's "Hook Response
Format" section and copied hook-to-hook (the annotation-poison class).

This shim is the single source of the valid shapes; hooks emit through it.
The regression floor lives in tests/test_hook_output_schema.py (source scan
+ behavioural runs).

Valid PreToolUse stdout shapes (Claude Code hooks contract):

- ``{}`` — silent allow (no-op).
- ``{"hookSpecificOutput": {"hookEventName": "PreToolUse",
     "permissionDecision": "allow"|"deny"|"ask",
     "permissionDecisionReason": "..."}}`` — modern decision, optional reason.
- ``{"decision": "approve"|"block", "reason": "..."}`` — legacy; avoid in
  new code, kept here only so the validator can recognise it.
- ``{"hookSpecificOutput": {"hookEventName": "PreToolUse",
     "additionalContext": "..."}, "systemMessage": "..."}`` -- an ADVISORY
  that takes no permission decision: context for Claude, plus an optional
  line for the operator. Build it with ``pretool_context``.

The PostToolUse advisory, built with ``posttool_context``:
``{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
"..."}, "systemMessage": "..."}``. A PostToolUse hook that answers with a bare
``systemMessage`` warns the operator only. Measured 2026-11-24: Gate E's
warning fired on a key-shaped value and never reached the model (GRIP#9497).
``is_schema_valid`` below checks the PreToolUse shapes only.

Why an advisory must not be an allow-with-reason. Per the vendor reference
(code.claude.com/docs/en/hooks, read 2026-10-02 against Claude Code 2.1.283),
``"allow"`` "skips the permission prompt", and its ``permissionDecisionReason``
is "written to the debug log only". An allow-with-reason advisory therefore
reaches neither Claude nor the operator, and it approves the very call it
warns about. ``additionalContext`` is "added to Claude's context alongside the
tool result"; ``systemMessage`` is a "Warning message shown to the user"; a
response with no decision leaves the normal permission flow untouched.
cc-gate-hook.py, quality-pretool-hook.py and premise-reproduce-gate.py already
emitted the context-only shape by hand, and the validator called it invalid
until this shape was added (2026-10-02).
"""
from __future__ import annotations

import json

VALID_PERMISSION_DECISIONS = ("allow", "deny", "ask")
LEGACY_DECISIONS = ("approve", "block")


def silent_allow() -> dict:
    """The no-op allow: an empty JSON object."""
    return {}


def pretool_decision(permission: str, reason: str = "") -> dict:
    """A modern PreToolUse decision (allow/deny/ask) with an optional reason."""
    if permission not in VALID_PERMISSION_DECISIONS:
        raise ValueError(
            f"invalid permissionDecision {permission!r}; "
        f"valid: {VALID_PERMISSION_DECISIONS}"
        )
    out: dict = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": permission,
        }
    }
    if reason:
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    return out


def _context_advisory(builder: str, event: str, context: str, system_message: str) -> dict:
    """Context for Claude under ``event``, plus an optional line for the operator."""
    if not isinstance(context, str) or not context:
        raise ValueError(f"{builder} needs a non-empty context string")
    out: dict = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }
    if system_message:
        out["systemMessage"] = system_message
    return out


def pretool_context(context: str, system_message: str = "") -> dict:
    """A PreToolUse advisory that takes NO permission decision.

    ``context`` reaches Claude as additionalContext; ``system_message``, when
    given, is shown to the operator. With no ``permissionDecision`` the
    harness's own permission flow applies unchanged: the call is neither
    approved nor denied by this hook. See the module docstring for why an
    advisory must not be an allow-with-reason.
    """
    return _context_advisory("pretool_context", "PreToolUse", context, system_message)


def posttool_context(context: str, system_message: str = "") -> dict:
    """A PostToolUse advisory: context for Claude, plus an optional operator line.

    ``context`` reaches Claude as additionalContext, which the vendor reference
    says is "added to Claude's context alongside the tool result".
    ``system_message``, when given, is shown to the operator and does not reach
    Claude, so a warning meant for the model belongs in ``context``. The tool
    has already run, so there is no decision to take.
    """
    return _context_advisory("posttool_context", "PostToolUse", context, system_message)


def _is_context_only(hso: dict) -> bool:
    """A PreToolUse block that carries context and takes no decision."""
    context = hso.get("additionalContext")
    return (
        hso.get("hookEventName") == "PreToolUse"
        and isinstance(context, str)
        and bool(context)
    )


def is_schema_valid(response: dict) -> bool:
    """True iff ``response`` is one of the harness-accepted PreToolUse shapes."""
    if not isinstance(response, dict):
        return False
    if not isinstance(response.get("systemMessage", ""), str):
        return False
    if response == {}:
        return True
    hso = response.get("hookSpecificOutput")
    if isinstance(hso, dict):
        if "permissionDecision" in hso:
            return hso["permissionDecision"] in VALID_PERMISSION_DECISIONS
        return _is_context_only(hso)
    if "decision" in response:
        return response["decision"] in LEGACY_DECISIONS
    return False


def emit(response: dict) -> None:
    """Print one hook response to stdout (the harness reads a single JSON object)."""
    print(json.dumps(response))
