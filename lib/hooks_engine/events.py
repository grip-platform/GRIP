"""Versioned, harness-neutral hook event contract.

Adapters own native payload parsing.  Policy code receives only the immutable
types in this module, which deliberately contain no Claude, OpenCode, or Codex
field names.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

SCHEMA_VERSION = "grip.hook-event/1"


class EventValidationError(ValueError):
    """Raised when an object cannot satisfy the canonical event contract."""


class Harness(str, Enum):
    CLAUDE = "claude"
    OPENCODE = "opencode"
    CODEX = "codex"


class EventName(str, Enum):
    CONFIG_CHANGE = "ConfigChange"
    ELICITATION = "Elicitation"
    ELICITATION_RESULT = "ElicitationResult"
    NOTIFICATION = "Notification"
    PERMISSION_REQUEST = "PermissionRequest"
    POST_COMPACT = "PostCompact"
    POST_TOOL_USE = "PostToolUse"
    PRE_COMPACT = "PreCompact"
    PRE_TOOL_USE = "PreToolUse"
    SESSION_END = "SessionEnd"
    SESSION_START = "SessionStart"
    STOP = "Stop"
    STOP_FAILURE = "StopFailure"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    TASK_COMPLETED = "TaskCompleted"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"


class CanonicalToolKind(str, Enum):
    COMMAND = "command"
    FILE_OPERATION = "file_operation"
    SEARCH = "search"
    NETWORK = "network"
    AGENT = "agent"
    PLAN = "plan"
    OTHER = "other"


class OperationKind(str, Enum):
    COMMAND = "command"
    FILE_DELTA = "file_delta"
    TOOL_INVOCATION = "tool_invocation"


class FileDeltaKind(str, Enum):
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"


JsonScalar = str | int | float | bool | None
FrozenJson = JsonScalar | tuple["FrozenJson", ...] | Mapping[str, "FrozenJson"]


def _require_optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise EventValidationError(f"{field_name} must be a non-empty string or null")
    return value


def _enum_value(enum_type: type[Enum], value: object, field_name: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(item.value for item in enum_type)
        raise EventValidationError(f"{field_name} must be one of: {choices}") from exc


def _freeze_json(value: object, field_name: str) -> FrozenJson:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EventValidationError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJson] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise EventValidationError(f"{field_name} contains a non-string key")
            frozen[key] = _freeze_json(item, f"{field_name}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{field_name}[{index}]") for index, item in enumerate(value))
    raise EventValidationError(f"{field_name} contains a non-JSON value")


def _thaw_json(value: FrozenJson) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _strict_keys(
    value: Mapping[str, object],
    *,
    allowed: set[str],
    required: set[str],
    field_name: str,
) -> None:
    missing = required - value.keys()
    extra = value.keys() - allowed
    if missing:
        raise EventValidationError(f"{field_name} missing required fields: {', '.join(sorted(missing))}")
    if extra:
        raise EventValidationError(f"{field_name} has unknown fields: {', '.join(sorted(extra))}")


@dataclass(frozen=True)
class ActorRef:
    """Canonical identity for the current actor or a related subagent."""

    id: str | None = None
    kind: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "kind", "name"):
            object.__setattr__(
                self,
                field_name,
                _require_optional_text(getattr(self, field_name), f"actor.{field_name}"),
            )
        if self.id is None and self.kind is None and self.name is None:
            raise EventValidationError("actor must contain at least one identity field")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ActorRef":
        _strict_keys(
            value,
            allowed={"id", "kind", "name"},
            required=set(),
            field_name="actor",
        )
        return cls(id=value.get("id"), kind=value.get("kind"), name=value.get("name"))

    def to_dict(self) -> dict[str, str | None]:
        return {"id": self.id, "kind": self.kind, "name": self.name}


@dataclass(frozen=True)
class CanonicalOperation:
    """One inspectable command, file delta, or nested tool invocation."""

    kind: OperationKind
    command: str | None = None
    path: str | None = None
    destination: str | None = None
    file_delta_kind: FileDeltaKind | None = None
    before: str | None = None
    after: str | None = None
    tool_name: str | None = None
    arguments: Mapping[str, FrozenJson] = field(default_factory=dict)
    metadata: Mapping[str, FrozenJson] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum_value(OperationKind, self.kind, "operation.kind"))
        for field_name in ("command", "path", "destination", "before", "after", "tool_name"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise EventValidationError(f"operation.{field_name} must be a string or null")
        delta_kind = self.file_delta_kind
        if delta_kind is not None:
            delta_kind = _enum_value(FileDeltaKind, delta_kind, "operation.file_delta_kind")
            object.__setattr__(self, "file_delta_kind", delta_kind)
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments, "operation.arguments"))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, "operation.metadata"))
        self._validate_shape()

    def _validate_shape(self) -> None:
        if self.kind is OperationKind.COMMAND:
            if not self.command:
                raise EventValidationError("command operation requires command")
            if any(
                value is not None
                for value in (
                    self.path,
                    self.destination,
                    self.file_delta_kind,
                    self.before,
                    self.after,
                    self.tool_name,
                )
            ):
                raise EventValidationError("command operation contains fields for another operation kind")
        elif self.kind is OperationKind.FILE_DELTA:
            if not self.path or self.file_delta_kind is None:
                raise EventValidationError("file_delta operation requires path and file_delta_kind")
            if self.command is not None or self.tool_name is not None:
                raise EventValidationError("file_delta operation contains fields for another operation kind")
            if self.file_delta_kind is FileDeltaKind.MOVE and not self.destination:
                raise EventValidationError("move file_delta requires destination")
            if self.destination and self.file_delta_kind is not FileDeltaKind.MOVE:
                raise EventValidationError("destination is valid only for move file_delta")
        elif self.kind is OperationKind.TOOL_INVOCATION:
            if not self.tool_name:
                raise EventValidationError("tool_invocation operation requires tool_name")
            if any(
                value is not None
                for value in (
                    self.command,
                    self.path,
                    self.destination,
                    self.file_delta_kind,
                    self.before,
                    self.after,
                )
            ):
                raise EventValidationError("tool_invocation contains fields for another operation kind")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "CanonicalOperation":
        allowed = {
            "kind",
            "command",
            "path",
            "destination",
            "file_delta_kind",
            "before",
            "after",
            "tool_name",
            "arguments",
            "metadata",
        }
        _strict_keys(value, allowed=allowed, required={"kind"}, field_name="operation")
        return cls(**dict(value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "command": self.command,
            "path": self.path,
            "destination": self.destination,
            "file_delta_kind": (self.file_delta_kind.value if self.file_delta_kind is not None else None),
            "before": self.before,
            "after": self.after,
            "tool_name": self.tool_name,
            "arguments": _thaw_json(self.arguments),
            "metadata": _thaw_json(self.metadata),
        }


def _freeze_mapping(value: object, field_name: str) -> Mapping[str, FrozenJson]:
    if not isinstance(value, Mapping):
        raise EventValidationError(f"{field_name} must be an object")
    frozen = _freeze_json(value, field_name)
    assert isinstance(frozen, Mapping)
    return frozen


@dataclass(frozen=True)
class HookTool:
    canonical_kind: CanonicalToolKind
    raw_name: str
    operations: tuple[CanonicalOperation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "canonical_kind",
            _enum_value(CanonicalToolKind, self.canonical_kind, "tool.canonical_kind"),
        )
        if not isinstance(self.raw_name, str) or not self.raw_name:
            raise EventValidationError("tool.raw_name must be a non-empty string")
        if not isinstance(self.operations, (list, tuple)):
            raise EventValidationError("tool.operations must be an array")
        operations = tuple(
            item
            if isinstance(item, CanonicalOperation)
            else CanonicalOperation.from_mapping(item)
            if isinstance(item, Mapping)
            else _raise_operation(index)
            for index, item in enumerate(self.operations)
        )
        object.__setattr__(self, "operations", operations)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "HookTool":
        _strict_keys(
            value,
            allowed={"canonical_kind", "raw_name", "operations"},
            required={"canonical_kind", "raw_name", "operations"},
            field_name="tool",
        )
        return cls(**dict(value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_kind": self.canonical_kind.value,
            "raw_name": self.raw_name,
            "operations": [operation.to_dict() for operation in self.operations],
        }


def _raise_operation(index: int) -> CanonicalOperation:
    raise EventValidationError(f"tool.operations[{index}] must be an object")


@dataclass(frozen=True)
class HookEvent:
    """Immutable canonical input consumed by GRIP hook policies."""

    schema_version: str
    harness: Harness
    event: EventName
    session_id: str
    turn_id: str | None
    cwd: str
    model: str | None = None
    actor: ActorRef | None = None
    subagent: ActorRef | None = None
    prompt: str | None = None
    tool: HookTool | None = None
    result: FrozenJson = None
    transcript_reference: str | None = None
    raw_metadata: Mapping[str, FrozenJson] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise EventValidationError(f"schema_version must be {SCHEMA_VERSION!r}")
        object.__setattr__(self, "harness", _enum_value(Harness, self.harness, "harness"))
        object.__setattr__(self, "event", _enum_value(EventName, self.event, "event"))
        for field_name in ("session_id", "cwd"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise EventValidationError(f"{field_name} must be a non-empty string")
        for field_name in ("turn_id", "model", "prompt", "transcript_reference"):
            object.__setattr__(
                self,
                field_name,
                _require_optional_text(getattr(self, field_name), field_name),
            )
        for field_name in ("actor", "subagent"):
            value = getattr(self, field_name)
            if isinstance(value, Mapping):
                object.__setattr__(self, field_name, ActorRef.from_mapping(value))
            elif value is not None and not isinstance(value, ActorRef):
                raise EventValidationError(f"{field_name} must be an actor object or null")
        if isinstance(self.tool, Mapping):
            object.__setattr__(self, "tool", HookTool.from_mapping(self.tool))
        elif self.tool is not None and not isinstance(self.tool, HookTool):
            raise EventValidationError("tool must be a tool object or null")
        object.__setattr__(self, "result", _freeze_json(self.result, "result"))
        object.__setattr__(
            self,
            "raw_metadata",
            _freeze_mapping(self.raw_metadata, "raw_metadata"),
        )
        self._validate_event_shape()

    def _validate_event_shape(self) -> None:
        tool_events = {EventName.PRE_TOOL_USE, EventName.POST_TOOL_USE}
        if self.event in tool_events and self.tool is None:
            raise EventValidationError(f"{self.event.value} requires tool")
        if self.event is EventName.USER_PROMPT_SUBMIT and self.prompt is None:
            raise EventValidationError("UserPromptSubmit requires prompt")
        if self.event in {EventName.SUBAGENT_START, EventName.SUBAGENT_STOP} and self.subagent is None:
            raise EventValidationError(f"{self.event.value} requires subagent")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "HookEvent":
        if not isinstance(value, Mapping):
            raise EventValidationError("event payload must be an object")
        fields = {
            "schema_version",
            "harness",
            "event",
            "session_id",
            "turn_id",
            "cwd",
            "model",
            "actor",
            "subagent",
            "prompt",
            "tool",
            "result",
            "transcript_reference",
            "raw_metadata",
        }
        required = {"schema_version", "harness", "event", "session_id", "turn_id", "cwd"}
        _strict_keys(value, allowed=fields, required=required, field_name="event")
        return cls(**dict(value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "harness": self.harness.value,
            "event": self.event.value,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "cwd": self.cwd,
            "model": self.model,
            "actor": self.actor.to_dict() if self.actor is not None else None,
            "subagent": self.subagent.to_dict() if self.subagent is not None else None,
            "prompt": self.prompt,
            "tool": self.tool.to_dict() if self.tool is not None else None,
            "result": _thaw_json(self.result),
            "transcript_reference": self.transcript_reference,
            "raw_metadata": _thaw_json(self.raw_metadata),
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def content_hash(self) -> str:
        digest = hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
        return f"sha256:{digest}"
