"""Canonical hook verdicts -> event-specific native response objects."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from .events import EventName, Harness
from .verdicts import HookVerdict


class ResponseSchemaError(ValueError):
    """Raised before an invalid or semantically unsupported response is emitted."""


def _event(value: str | EventName) -> EventName:
    try:
        return EventName(value)
    except (TypeError, ValueError) as exc:
        raise ResponseSchemaError(f"unsupported hook event: {value!r}") from exc


def _reason(verdict: HookVerdict) -> str:
    reason = verdict.reason.strip()
    if verdict.disposition == "deny" and not reason:
        raise ResponseSchemaError("deny verdict requires a non-empty reason")
    return reason


def _context_output(event: EventName, verdict: HookVerdict) -> dict[str, Any]:
    if not verdict.additional_context:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": event.value,
            "additionalContext": verdict.additional_context,
        }
    }


def _universal(verdict: HookVerdict) -> dict[str, Any]:
    response: dict[str, Any] = {}
    if verdict.system_message:
        response["systemMessage"] = verdict.system_message
    return response


def _blocking_output(verdict: HookVerdict) -> dict[str, Any]:
    response = _universal(verdict)
    if verdict.disposition == "deny":
        response.update({"decision": "block", "reason": _reason(verdict)})
    return response


def _pre_tool_output(verdict: HookVerdict, *, codex: bool) -> dict[str, Any]:
    if codex and verdict.updated_input is not None:
        raise ResponseSchemaError("Codex PreToolUse does not support updatedInput")
    response = _universal(verdict)
    hso: dict[str, Any] = {
        "hookEventName": EventName.PRE_TOOL_USE.value,
        "permissionDecision": "deny" if verdict.disposition == "deny" else "allow",
    }
    if verdict.reason:
        hso["permissionDecisionReason"] = verdict.reason
    if verdict.updated_input is not None:
        hso["updatedInput"] = verdict.to_dict()["updated_input"]
    # Current Codex parses but rejects PreToolUse additionalContext.  Surface
    # advisory text via its supported universal message instead.
    if verdict.additional_context:
        if codex:
            response.setdefault("systemMessage", verdict.additional_context)
        else:
            hso["additionalContext"] = verdict.additional_context
    response["hookSpecificOutput"] = hso
    return response


def _permission_output(verdict: HookVerdict, *, codex: bool) -> dict[str, Any]:
    if verdict.disposition in {"continue", "feedback"}:
        response = _universal(verdict)
        if verdict.additional_context:
            response.setdefault("systemMessage", verdict.additional_context)
        return response
    if codex and verdict.updated_input is not None:
        raise ResponseSchemaError("Codex PermissionRequest does not support updatedInput")
    decision: dict[str, Any] = {"behavior": "deny" if verdict.disposition == "deny" else "allow"}
    if verdict.reason:
        decision["message"] = verdict.reason
    if verdict.updated_input is not None:
        decision["updatedInput"] = verdict.to_dict()["updated_input"]
    response = _universal(verdict)
    response["hookSpecificOutput"] = {
        "hookEventName": EventName.PERMISSION_REQUEST.value,
        "decision": decision,
    }
    return response


def _post_tool_output(verdict: HookVerdict) -> dict[str, Any]:
    response = _blocking_output(verdict)
    if verdict.additional_context:
        response["hookSpecificOutput"] = {
            "hookEventName": EventName.POST_TOOL_USE.value,
            "additionalContext": verdict.additional_context,
        }
    return response


_CONTEXT_EVENTS = {
    EventName.SESSION_START,
    EventName.SUBAGENT_START,
}
_BLOCK_EVENTS = {
    EventName.STOP,
    EventName.SUBAGENT_STOP,
    EventName.CONFIG_CHANGE,
}
_STATELESS_EVENTS = {
    EventName.PRE_COMPACT,
    EventName.POST_COMPACT,
}
_NO_CONTROL_EVENTS = {
    EventName.NOTIFICATION,
    EventName.SESSION_END,
    EventName.STOP_FAILURE,
}
_CODEX_EVENTS = {
    EventName.SESSION_START,
    EventName.SESSION_END,
    EventName.PRE_TOOL_USE,
    EventName.PERMISSION_REQUEST,
    EventName.POST_TOOL_USE,
    EventName.PRE_COMPACT,
    EventName.POST_COMPACT,
    EventName.USER_PROMPT_SUBMIT,
    EventName.SUBAGENT_START,
    EventName.SUBAGENT_STOP,
    EventName.STOP,
}


def _render_claude_only(event: EventName, verdict: HookVerdict) -> dict[str, Any]:
    if event in {EventName.ELICITATION, EventName.ELICITATION_RESULT}:
        response = _universal(verdict)
        if verdict.disposition == "deny":
            response["hookSpecificOutput"] = {
                "hookEventName": event.value,
                "action": "decline",
            }
            if verdict.reason:
                response.setdefault("systemMessage", verdict.reason)
        return response
    if event is EventName.TASK_COMPLETED:
        if verdict.disposition == "deny":
            return {
                **_universal(verdict),
                "continue": False,
                "stopReason": _reason(verdict),
            }
        return _universal(verdict)
    if event in _NO_CONTROL_EVENTS:
        if verdict.disposition == "deny":
            raise ResponseSchemaError(f"Claude cannot deny {event.value}")
        return {}
    raise ResponseSchemaError(f"{event.value} has no command-hook response schema")


def _render_claude_family(event: EventName, verdict: HookVerdict, *, codex: bool) -> dict[str, Any]:
    if event is EventName.PRE_TOOL_USE:
        return _pre_tool_output(verdict, codex=codex)
    if event is EventName.PERMISSION_REQUEST:
        return _permission_output(verdict, codex=codex)
    if event is EventName.POST_TOOL_USE:
        return _post_tool_output(verdict)
    if event in _CONTEXT_EVENTS:
        response = _universal(verdict)
        response.update(_context_output(event, verdict))
        if verdict.disposition == "deny":
            response.update({"continue": False, "stopReason": _reason(verdict)})
        return response
    if event is EventName.USER_PROMPT_SUBMIT:
        response = _blocking_output(verdict)
        response.update(_context_output(event, verdict))
        return response
    if event in _BLOCK_EVENTS:
        return _blocking_output(verdict)
    if event in _STATELESS_EVENTS:
        if verdict.disposition == "deny":
            return {
                **_universal(verdict),
                "continue": False,
                "stopReason": _reason(verdict),
            }
        return _universal(verdict)
    if event is EventName.SESSION_END:
        # SessionEnd is side-effect only; both hosts ignore command stdout.
        if verdict.disposition == "deny":
            host = "Codex" if codex else "Claude"
            raise ResponseSchemaError(f"{host} cannot deny SessionEnd")
        return {}
    if codex:
        raise ResponseSchemaError(f"Codex does not support {event.value}")
    return _render_claude_only(event, verdict)


def render_claude(event: str | EventName, verdict: HookVerdict) -> dict[str, Any]:
    """Render a Claude Code command-hook response."""

    result = _render_claude_family(_event(event), verdict, codex=False)
    validate_rendered_response(Harness.CLAUDE, event, result)
    return result


def render_codex(event: str | EventName, verdict: HookVerdict) -> dict[str, Any]:
    """Render a strict Codex response, excluding parsed-but-unsupported fields."""

    native_event = _event(event)
    if native_event not in _CODEX_EVENTS:
        raise ResponseSchemaError(f"Codex does not support {native_event.value}")
    result = _render_claude_family(native_event, verdict, codex=True)
    validate_rendered_response(Harness.CODEX, native_event, result)
    return result


def render_opencode(event: str | EventName, verdict: HookVerdict) -> dict[str, Any]:
    """Describe the documented OpenCode callback action.

    The JavaScript adapter applies this object: ``throw`` denies a before-hook;
    ``input`` is the supported mutable tool input; advisory messages are
    returned for the adapter to surface.  Python never pretends an OpenCode
    callback has a JSON-stdout protocol.
    """

    native_event = _event(event)
    response: dict[str, Any] = {}
    if verdict.disposition == "deny":
        if native_event not in {
            EventName.PRE_TOOL_USE,
            EventName.PERMISSION_REQUEST,
            EventName.USER_PROMPT_SUBMIT,
            EventName.STOP,
            EventName.SUBAGENT_STOP,
        }:
            raise ResponseSchemaError(f"OpenCode cannot deny {native_event.value}")
        response["throw"] = _reason(verdict)
    if verdict.updated_input is not None:
        if native_event is not EventName.PRE_TOOL_USE:
            raise ResponseSchemaError("OpenCode input rewriting is only valid before tool use")
        response["input"] = verdict.to_dict()["updated_input"]
    message = verdict.system_message or verdict.additional_context
    if message:
        response["message"] = message
    validate_rendered_response(Harness.OPENCODE, native_event, response)
    return response


def render(harness: str | Harness, event: str | EventName, verdict: HookVerdict) -> dict[str, Any]:
    """Dispatch to an explicit host renderer."""

    try:
        host = Harness(harness)
    except (TypeError, ValueError) as exc:
        raise ResponseSchemaError(f"unsupported hook harness: {harness!r}") from exc
    renderer: Callable[[str | EventName, HookVerdict], dict[str, Any]] = {
        Harness.CLAUDE: render_claude,
        Harness.CODEX: render_codex,
        Harness.OPENCODE: render_opencode,
    }[host]
    return renderer(event, verdict)


def _validate_hso(event: EventName, hso: object) -> None:
    if not isinstance(hso, Mapping):
        raise ResponseSchemaError("hookSpecificOutput must be an object")
    if hso.get("hookEventName") != event.value:
        raise ResponseSchemaError("hookSpecificOutput event does not match response event")
    event_fields = {
        EventName.PRE_TOOL_USE: {
            "hookEventName",
            "permissionDecision",
            "permissionDecisionReason",
            "updatedInput",
            "additionalContext",
        },
        EventName.PERMISSION_REQUEST: {"hookEventName", "decision"},
        EventName.POST_TOOL_USE: {
            "hookEventName",
            "additionalContext",
            "updatedMCPToolOutput",
        },
        EventName.SESSION_START: {"hookEventName", "additionalContext"},
        EventName.SUBAGENT_START: {"hookEventName", "additionalContext"},
        EventName.USER_PROMPT_SUBMIT: {"hookEventName", "additionalContext"},
        EventName.ELICITATION: {"hookEventName", "action", "content"},
        EventName.ELICITATION_RESULT: {"hookEventName", "action", "content"},
    }
    unknown = set(hso) - event_fields.get(event, set())
    if unknown:
        raise ResponseSchemaError(f"unknown {event.value} hookSpecificOutput fields: {sorted(unknown)}")


def _allowed_top_level_fields(event: EventName) -> set[str]:
    universal = {"continue", "stopReason", "suppressOutput", "systemMessage"}
    fields = {
        EventName.PRE_TOOL_USE: {"decision", "reason", "hookSpecificOutput"},
        EventName.PERMISSION_REQUEST: {"hookSpecificOutput"},
        EventName.POST_TOOL_USE: {"decision", "reason", "hookSpecificOutput"},
        EventName.PRE_COMPACT: set(),
        EventName.POST_COMPACT: set(),
        EventName.SESSION_START: {"hookSpecificOutput"},
        EventName.SESSION_END: set(),
        EventName.USER_PROMPT_SUBMIT: {"decision", "reason", "hookSpecificOutput"},
        EventName.SUBAGENT_START: {"hookSpecificOutput"},
        EventName.SUBAGENT_STOP: {"decision", "reason"},
        EventName.STOP: {"decision", "reason"},
        EventName.CONFIG_CHANGE: {"decision", "reason"},
        EventName.ELICITATION: {"hookSpecificOutput"},
        EventName.ELICITATION_RESULT: {"hookSpecificOutput"},
        EventName.TASK_COMPLETED: set(),
        EventName.NOTIFICATION: set(),
        EventName.STOP_FAILURE: set(),
    }
    if event in _NO_CONTROL_EVENTS:
        return set()
    return universal | fields.get(event, set())


def _validate_pre_tool_hso(host: Harness, hso: object) -> None:
    if not isinstance(hso, Mapping):
        return
    decision = hso.get("permissionDecision")
    if decision not in {"allow", "deny"}:
        raise ResponseSchemaError("PreToolUse decision must be allow or deny")
    if host is Harness.CODEX and ("updatedInput" in hso or "additionalContext" in hso):
        raise ResponseSchemaError("Codex PreToolUse contains unsupported fields")


def _validate_permission_hso(host: Harness, hso: object) -> None:
    if not isinstance(hso, Mapping):
        return
    decision = hso.get("decision")
    if not isinstance(decision, Mapping) or decision.get("behavior") not in {
        "allow",
        "deny",
    }:
        raise ResponseSchemaError("PermissionRequest requires nested allow/deny decision")
    allowed = {"behavior", "message", "updatedInput", "updatedPermissions", "interrupt"}
    unknown = set(decision) - allowed
    if unknown:
        raise ResponseSchemaError(f"unknown PermissionRequest decision fields: {sorted(unknown)}")
    unsupported = "updatedInput" in decision or "updatedPermissions" in decision or decision.get("interrupt") is True
    if host is Harness.CODEX and unsupported:
        raise ResponseSchemaError("Codex PermissionRequest contains unsupported decision fields")


def validate_rendered_response(
    harness: str | Harness,
    event: str | EventName,
    response: object,
) -> None:
    """Validate the narrow schema emitted by this module.

    Host schemas remain authoritative.  This local guard rejects unknown keys
    and the event/decision mistakes most likely to make a host ignore a gate.
    """

    try:
        host = Harness(harness)
    except (TypeError, ValueError) as exc:
        raise ResponseSchemaError(f"unsupported hook harness: {harness!r}") from exc
    native_event = _event(event)
    if not isinstance(response, Mapping):
        raise ResponseSchemaError("hook response must be an object")
    if host is Harness.OPENCODE:
        unknown = set(response) - {"throw", "input", "message"}
        if unknown:
            raise ResponseSchemaError(f"unknown OpenCode response fields: {sorted(unknown)}")
        return
    allowed = _allowed_top_level_fields(native_event)
    unknown = set(response) - allowed
    if unknown:
        raise ResponseSchemaError(f"unknown hook response fields: {sorted(unknown)}")
    hso = response.get("hookSpecificOutput")
    if hso is not None:
        _validate_hso(native_event, hso)
    if response.get("decision") == "block" and not response.get("reason"):
        raise ResponseSchemaError("decision:block requires reason")
    if native_event is EventName.PRE_TOOL_USE:
        _validate_pre_tool_hso(host, hso)
    if native_event is EventName.PERMISSION_REQUEST:
        _validate_permission_hso(host, hso)
    if native_event in {EventName.ELICITATION, EventName.ELICITATION_RESULT}:
        if hso is not None and (
            not isinstance(hso, Mapping) or hso.get("action") not in {"accept", "decline", "cancel"}
        ):
            raise ResponseSchemaError(f"{native_event.value} requires accept/decline/cancel action")
