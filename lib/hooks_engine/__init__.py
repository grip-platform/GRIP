"""GRIP's harness-agnostic deterministic hook engine."""

from .audit import AuditReceipt, create_audit_receipt, redact, verify_audit_receipt
from .engine import EngineResult, evaluate
from .events import (
    SCHEMA_VERSION,
    ActorRef,
    CanonicalOperation,
    CanonicalToolKind,
    EventName,
    EventValidationError,
    FileDeltaKind,
    Harness,
    HookEvent,
    HookTool,
    OperationKind,
)
from .runtime import RuntimeResult, process
from .verdicts import HookVerdict, aggregate_verdicts, select_policies

__all__ = [
    "SCHEMA_VERSION",
    "ActorRef",
    "AuditReceipt",
    "CanonicalOperation",
    "CanonicalToolKind",
    "EngineResult",
    "EventName",
    "EventValidationError",
    "FileDeltaKind",
    "Harness",
    "HookEvent",
    "HookTool",
    "HookVerdict",
    "OperationKind",
    "RuntimeResult",
    "aggregate_verdicts",
    "create_audit_receipt",
    "evaluate",
    "process",
    "redact",
    "select_policies",
    "verify_audit_receipt",
]
