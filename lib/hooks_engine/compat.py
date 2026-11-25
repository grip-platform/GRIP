"""Pure compatibility translations for existing Claude-shaped hook scripts.

This module intentionally does not execute subprocesses.  A host-specific
runner may pass the generated input to an existing script, then translate its
exit status and parsed stdout with :func:`verdict_from_claude_result`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .events import EventName, HookEvent, OperationKind
from .verdicts import HookVerdict


class CompatibilityError(ValueError):
    """Raised when legacy output is ambiguous or structurally invalid."""


def _thaw(value: object) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _legacy_tool_input(event: HookEvent) -> dict[str, Any]:
    if event.tool is None or not event.tool.operations:
        return {}
    operation = event.tool.operations[0]
    if operation.kind is OperationKind.COMMAND:
        arguments = _thaw(operation.arguments)
        arguments.setdefault("command", operation.command)
        return arguments
    if operation.kind is OperationKind.TOOL_INVOCATION:
        return _thaw(operation.arguments)
    # Wave 2 will own reconstructed file-delta payloads.  Existing native
    # Write/Edit payloads are represented as generic invocations by Wave 1.
    return {}


def claude_payload_from_event(event: HookEvent) -> dict[str, Any]:
    """Produce the smallest valid Claude-shaped input for a legacy script."""

    payload: dict[str, Any] = {
        "session_id": event.session_id,
        "transcript_path": event.transcript_reference,
        "cwd": event.cwd,
        "hook_event_name": event.event.value,
    }
    if event.turn_id is not None:
        payload["turn_id"] = event.turn_id
    if event.model is not None:
        payload["model"] = event.model
    if event.prompt is not None:
        payload["prompt"] = event.prompt
    if event.tool is not None:
        payload.update(
            {
                "tool_name": event.tool.raw_name,
                "tool_input": _legacy_tool_input(event),
            }
        )
    if event.event is EventName.POST_TOOL_USE:
        payload["tool_response"] = _thaw(event.result)
    subject = event.subagent or event.actor
    if subject is not None:
        if subject.id is not None:
            payload["agent_id"] = subject.id
        if subject.kind is not None:
            payload["agent_type"] = subject.kind
    # Canonical fields win.  Native-only values such as permission_mode,
    # trigger, source, and tool_use_id remain available to compatible scripts.
    for key, value in _thaw(event.raw_metadata).items():
        payload.setdefault(key, value)
    return payload


def _object(output: object) -> dict[str, Any]:
    if output is None or output == "":
        return {}
    if not isinstance(output, Mapping):
        raise CompatibilityError("legacy Claude output must be an object")
    return dict(output)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _legacy_event(value: str | EventName) -> EventName:
    try:
        return EventName(value)
    except (TypeError, ValueError) as exc:
        raise CompatibilityError(f"unsupported legacy hook event: {value!r}") from exc


def _legacy_hso(native: Mapping[str, Any], event: EventName) -> dict[str, Any]:
    value = native.get("hookSpecificOutput", {})
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise CompatibilityError("hookSpecificOutput must be an object")
    hso = dict(value)
    declared_event = hso.get("hookEventName")
    if declared_event is not None and declared_event != event.value:
        raise CompatibilityError("legacy output hookEventName does not match event")
    return hso


def _legacy_decision(native: Mapping[str, Any], hso: Mapping[str, Any]) -> tuple[str, str, tuple[Any, ...]]:
    reason = _text(native.get("reason") or hso.get("permissionDecisionReason") or native.get("stopReason"))
    permission = hso.get("permissionDecision")
    blocked = native.get("decision") in {"block", "deny"} or permission == "deny"
    evidence: tuple[Any, ...] = ()
    if permission == "ask":
        blocked = True
        reason = reason or "Legacy policy requires interactive approval"
        evidence = ({"legacy_permission_decision": "ask"},)
    if native.get("continue") is False:
        blocked = True
        reason = reason or "Legacy hook stopped processing"
    if blocked and not reason:
        raise CompatibilityError("legacy denial requires a non-empty reason")
    if blocked:
        return "deny", reason, evidence
    if _text(hso.get("additionalContext")) or _text(native.get("systemMessage")):
        return "feedback", reason, evidence
    if permission == "allow" or native.get("decision") in {"allow", "approve"}:
        return "allow", reason, evidence
    return "continue", reason, evidence


def _exit_two_verdict(stderr: str, policy_id: str, severity: str, failure_mode: str) -> HookVerdict:
    reason = _text(stderr)
    if not reason:
        raise CompatibilityError("legacy exit 2 requires a denial reason on stderr")
    return HookVerdict(
        "deny",
        reason=reason,
        policy_id=policy_id,
        severity=severity,
        failure_mode=failure_mode,
        evidence=({"legacy_exit_code": 2},),
    )


def verdict_from_claude_result(
    output: object,
    *,
    event: str | EventName,
    exit_code: int = 0,
    stderr: str = "",
    policy_id: str = "legacy.claude",
    severity: str = "advisory",
    failure_mode: str = "open",
) -> HookVerdict:
    """Translate one already-executed Claude script result.

    Exit ``2`` is an explicit denial.  Other non-zero exits are runtime errors
    for the caller's registry failure policy to handle, not verdicts.  Legacy
    PreToolUse ``ask`` cannot be represented by the cross-host contract and is
    conservatively translated to deny instead of silently approving it.
    """

    event_name = _legacy_event(event)
    if exit_code == 2:
        return _exit_two_verdict(stderr, policy_id, severity, failure_mode)
    if exit_code != 0:
        raise CompatibilityError(f"legacy hook exited with code {exit_code}")
    native = _object(output)
    hso = _legacy_hso(native, event_name)
    additional_context = _text(hso.get("additionalContext"))
    system_message = _text(native.get("systemMessage"))
    updated_input = hso.get("updatedInput")
    if updated_input is not None and not isinstance(updated_input, Mapping):
        raise CompatibilityError("legacy updatedInput must be an object")
    disposition, reason, evidence = _legacy_decision(native, hso)
    return HookVerdict(
        disposition,
        reason=reason,
        additional_context=additional_context,
        system_message=system_message,
        updated_input=dict(updated_input) if updated_input is not None else None,
        policy_id=policy_id,
        severity=severity,
        failure_mode=failure_mode,
        evidence=evidence,
    )
