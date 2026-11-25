"""Native hook payloads -> the immutable GRIP hook event contract.

This boundary is deliberately lossless but not permissive: common fields are
promoted into :class:`HookEvent`, while every remaining native field is kept
only under ``raw_metadata``.  Timeout units are configuration concerns and are
never inferred here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .events import (
    SCHEMA_VERSION,
    CanonicalToolKind,
    EventName,
    EventValidationError,
    Harness,
    HookEvent,
    OperationKind,
)


class AdapterError(EventValidationError):
    """Raised when a native payload cannot be normalised without guessing."""


_TOOL_EVENTS = {EventName.PRE_TOOL_USE, EventName.PERMISSION_REQUEST, EventName.POST_TOOL_USE}
_SUBAGENT_EVENTS = {EventName.SUBAGENT_START, EventName.SUBAGENT_STOP}
_COMMAND_TOOLS = frozenset({"bash", "shell", "shell_command", "exec", "exec_command", "unified_exec"})
_SEARCH_TOOLS = frozenset({"glob", "grep", "list", "read", "read_file", "search"})
_NETWORK_TOOLS = frozenset({"webfetch", "web_fetch", "websearch", "web_search"})
_AGENT_TOOLS = frozenset({"agent", "task", "spawn_agent"})
_PLAN_TOOLS = frozenset({"update_plan", "todowrite"})

_COMMON_ALIASES = {
    "session_id": ("session_id", "sessionId"),
    "turn_id": ("turn_id", "turnId"),
    "cwd": ("cwd", "directory", "worktree"),
    "model": ("model",),
    "transcript_reference": ("transcript_path", "transcriptPath"),
}

_OPENCODE_EVENT_MAP = {
    "tool.execute.before": EventName.PRE_TOOL_USE,
    "tool.execute.after": EventName.POST_TOOL_USE,
    "permission.asked": EventName.PERMISSION_REQUEST,
    "session.created": EventName.SESSION_START,
    "session.compacted": EventName.POST_COMPACT,
    "session.deleted": EventName.SESSION_END,
    "session.error": EventName.STOP_FAILURE,
    "session.idle": EventName.STOP,
    "chat.message": EventName.USER_PROMPT_SUBMIT,
}


def _mapping(payload: object) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AdapterError("hook payload must be an object")
    return dict(payload)


def _first(payload: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    return next((payload[key] for key in aliases if key in payload), None)


def _non_empty_text(value: object, name: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise AdapterError(f"{name} must be a non-empty string")
    return value


def _event_name(value: object) -> EventName:
    try:
        return EventName(value)
    except (TypeError, ValueError) as exc:
        raise AdapterError(f"unsupported hook event: {value!r}") from exc


def _operation_for_tool(raw_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    lowered = raw_name.lower()
    command = arguments.get("command")
    if lowered in _COMMAND_TOOLS and isinstance(command, str) and command:
        return {
            "kind": OperationKind.COMMAND.value,
            "command": command,
            "arguments": dict(arguments),
        }
    return {
        "kind": OperationKind.TOOL_INVOCATION.value,
        "tool_name": raw_name,
        "arguments": dict(arguments),
    }


def _tool_kind(raw_name: str) -> CanonicalToolKind:
    lowered = raw_name.lower()
    if lowered in _COMMAND_TOOLS:
        return CanonicalToolKind.COMMAND
    if lowered in {"edit", "patch", "write", "apply_patch"}:
        return CanonicalToolKind.FILE_OPERATION
    if lowered in _SEARCH_TOOLS:
        return CanonicalToolKind.SEARCH
    if lowered in _NETWORK_TOOLS:
        return CanonicalToolKind.NETWORK
    if lowered in _AGENT_TOOLS:
        return CanonicalToolKind.AGENT
    if lowered in _PLAN_TOOLS:
        return CanonicalToolKind.PLAN
    return CanonicalToolKind.OTHER


def _native_tool(payload: Mapping[str, Any], event: EventName) -> dict[str, Any] | None:
    if event not in _TOOL_EVENTS:
        return None
    raw_name = _first(payload, ("tool_name", "toolName", "tool"))
    raw_name = _non_empty_text(raw_name, "tool_name", required=True)
    arguments = _first(payload, ("tool_input", "toolInput", "args", "input"))
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, Mapping):
        raise AdapterError("tool input must be an object")
    return {
        "canonical_kind": _tool_kind(raw_name).value,
        "raw_name": raw_name,
        # apply_patch remains a generic invocation until Wave 2's safe parser.
        "operations": [_operation_for_tool(raw_name, arguments)],
    }


def _actor(payload: Mapping[str, Any], event: EventName) -> dict[str, str] | None:
    if event not in _SUBAGENT_EVENTS and not (payload.get("agent_id") or payload.get("agentId")):
        return None
    actor_id = _first(payload, ("agent_id", "agentId"))
    actor_kind = _first(payload, ("agent_type", "agentType"))
    if event in _SUBAGENT_EVENTS and not (actor_id or actor_kind):
        raise AdapterError(f"{event.value} requires agent_id or agent_type")
    result: dict[str, str] = {}
    if actor_id is not None:
        result["id"] = _non_empty_text(actor_id, "agent_id", required=True)
    if actor_kind is not None:
        result["kind"] = _non_empty_text(actor_kind, "agent_type", required=True)
    return result or None


def _consumed_keys(event: EventName) -> set[str]:
    consumed = {"hook_event_name", "hookEventName", "event_type", "type"}
    for aliases in _COMMON_ALIASES.values():
        consumed.update(aliases)
    if event in _TOOL_EVENTS:
        consumed.update(
            {
                "tool_name",
                "toolName",
                "tool",
                "tool_input",
                "toolInput",
                "args",
                "input",
            }
        )
    if event is EventName.POST_TOOL_USE:
        consumed.update({"tool_response", "toolResponse", "result", "output"})
    if event is EventName.USER_PROMPT_SUBMIT:
        consumed.update({"prompt", "message"})
    if event in _SUBAGENT_EVENTS:
        consumed.update({"agent_id", "agentId", "agent_type", "agentType"})
    else:
        # Codex tool and lifecycle events may execute inside a subagent.  In
        # that case these fields are promoted to canonical ``actor``.
        consumed.update({"agent_id", "agentId", "agent_type", "agentType"})
    return consumed


def _canonical(
    harness: Harness,
    event: EventName,
    payload: Mapping[str, Any],
) -> HookEvent:
    session_id = _non_empty_text(_first(payload, _COMMON_ALIASES["session_id"]), "session_id", required=True)
    cwd = _non_empty_text(_first(payload, _COMMON_ALIASES["cwd"]), "cwd", required=True)
    turn_id = _non_empty_text(_first(payload, _COMMON_ALIASES["turn_id"]), "turn_id")
    model = _non_empty_text(_first(payload, _COMMON_ALIASES["model"]), "model")
    transcript = _non_empty_text(_first(payload, _COMMON_ALIASES["transcript_reference"]), "transcript_path")
    prompt = None
    if event is EventName.USER_PROMPT_SUBMIT:
        prompt = _non_empty_text(_first(payload, ("prompt", "message")), "prompt", required=True)
    result = None
    if event is EventName.POST_TOOL_USE:
        result = _first(payload, ("tool_response", "toolResponse", "result", "output"))
    subagent = _actor(payload, event)
    raw_metadata = {key: value for key, value in payload.items() if key not in _consumed_keys(event)}
    return HookEvent.from_mapping(
        {
            "schema_version": SCHEMA_VERSION,
            "harness": harness.value,
            "event": event.value,
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": cwd,
            "model": model,
            "actor": subagent if event not in _SUBAGENT_EVENTS else None,
            "subagent": subagent if event in _SUBAGENT_EVENTS else None,
            "prompt": prompt,
            "tool": _native_tool(payload, event),
            "result": result,
            "transcript_reference": transcript,
            "raw_metadata": raw_metadata,
        }
    )


def _native_event(payload: Mapping[str, Any], explicit_event: str | EventName | None) -> EventName:
    native = _first(payload, ("hook_event_name", "hookEventName"))
    if explicit_event is not None:
        requested = _event_name(explicit_event.value if isinstance(explicit_event, EventName) else explicit_event)
        if native is not None and _event_name(native) is not requested:
            raise AdapterError("explicit event does not match payload hook_event_name")
        return requested
    return _event_name(native)


def adapt_claude(payload: object, event: str | EventName | None = None) -> HookEvent:
    """Adapt one Claude Code command-hook payload."""

    native = _mapping(payload)
    return _canonical(Harness.CLAUDE, _native_event(native, event), native)


def adapt_codex(payload: object, event: str | EventName | None = None) -> HookEvent:
    """Adapt one Codex command-hook payload without interpreting timeout units."""

    native = _mapping(payload)
    return _canonical(Harness.CODEX, _native_event(native, event), native)


def _flatten_opencode(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten documented OpenCode callback/event wrappers, without mutation."""

    flattened = dict(payload)
    event = payload.get("event")
    if isinstance(event, Mapping):
        properties = event.get("properties")
        if isinstance(properties, Mapping):
            flattened.update(properties)
        flattened.setdefault("event_type", event.get("type"))
    output = payload.get("output")
    if isinstance(output, Mapping):
        for key, value in output.items():
            # OpenCode documents mutable before-hook arguments on ``output``;
            # they are the effective arguments when both callback objects carry
            # a value.  The source payload remains untouched.
            flattened[key] = value
    info = flattened.get("info")
    if isinstance(info, Mapping):
        flattened.setdefault("session_id", info.get("sessionID") or info.get("id"))
    flattened.setdefault("session_id", payload.get("sessionID"))
    flattened.setdefault("turn_id", payload.get("messageID") or payload.get("callID"))
    return flattened


def adapt_opencode(payload: object, event: str | EventName | None = None) -> HookEvent:
    """Adapt a documented OpenCode plugin callback or event notification."""

    native = _flatten_opencode(_mapping(payload))
    if event is None:
        event_type = _first(native, ("event_type", "type"))
        try:
            canonical_event = _OPENCODE_EVENT_MAP[str(event_type)]
        except KeyError as exc:
            raise AdapterError(f"unsupported OpenCode event: {event_type!r}") from exc
    else:
        canonical_event = _event_name(event.value if isinstance(event, EventName) else event)
    return _canonical(Harness.OPENCODE, canonical_event, native)


def adapt(
    harness: str | Harness,
    payload: object,
    event: str | EventName | None = None,
) -> HookEvent:
    """Dispatch to one explicit host adapter."""

    try:
        host = Harness(harness)
    except (TypeError, ValueError) as exc:
        raise AdapterError(f"unsupported hook harness: {harness!r}") from exc
    return {
        Harness.CLAUDE: adapt_claude,
        Harness.CODEX: adapt_codex,
        Harness.OPENCODE: adapt_opencode,
    }[host](payload, event)
