"""Deterministic policy selection and canonical hook verdict aggregation.

This module has no harness knowledge.  Adapters translate host payloads before
calling it and render the resulting :class:`HookVerdict` afterwards.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Callable, Iterable, Literal, Mapping

Disposition = Literal["allow", "deny", "feedback", "continue"]
DISPOSITIONS = frozenset({"allow", "deny", "feedback", "continue"})
FAILURE_MODES = frozenset({"open", "closed"})
SEVERITIES = frozenset({"hard", "soft", "advisory"})

# A larger rank can never be rescued by a smaller one.
_DISPOSITION_RANK = {
    "continue": 0,
    "allow": 1,
    "feedback": 2,
    "deny": 3,
}
_SEVERITY_RANK = {"advisory": 0, "soft": 1, "hard": 2}


def _freeze(value: Any) -> Any:
    """Copy a JSON value into an immutable, deterministic representation."""

    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        frozen = {key: _freeze(item) for key, item in sorted(value.items())}
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class HookVerdict:
    """Immutable, harness-independent result from one hook policy."""

    disposition: Disposition
    reason: str = ""
    additional_context: str = ""
    system_message: str = ""
    updated_input: Mapping[str, Any] | None = None
    policy_id: str = "policy.engine"
    severity: str = "advisory"
    failure_mode: str = "open"
    evidence: Any = ()
    audit_reference: str = ""

    def __post_init__(self) -> None:
        if self.disposition not in DISPOSITIONS:
            raise ValueError(f"invalid hook disposition: {self.disposition!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"invalid hook severity: {self.severity!r}")
        if self.failure_mode not in FAILURE_MODES:
            raise ValueError(f"invalid hook failure mode: {self.failure_mode!r}")
        if not isinstance(self.policy_id, str) or not self.policy_id:
            raise ValueError("policy_id must be a non-empty string")
        for field_name in (
            "reason",
            "additional_context",
            "system_message",
            "audit_reference",
        ):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be a string")
        if self.updated_input is not None:
            if not isinstance(self.updated_input, Mapping):
                raise TypeError("updated_input must be a mapping or None")
            object.__setattr__(self, "updated_input", _freeze(self.updated_input))
        object.__setattr__(self, "evidence", _freeze(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        """Return a mutable JSON representation without native host keys."""

        return {
            "disposition": self.disposition,
            "reason": self.reason,
            "additional_context": self.additional_context,
            "system_message": self.system_message,
            "updated_input": _thaw(self.updated_input),
            "policy_id": self.policy_id,
            "severity": self.severity,
            "failure_mode": self.failure_mode,
            "evidence": _thaw(self.evidence),
            "audit_reference": self.audit_reference,
        }


def normalise_disposition(
    verdict: HookVerdict,
    event: str | None = None,
    *,
    normaliser: Callable[[HookVerdict, str | None], HookVerdict] | None = None,
) -> HookVerdict:
    """Apply an optional event-aware semantic normaliser.

    The canonical engine deliberately supplies no host-specific mapping.
    Renderers can inject an explicit normaliser for event surfaces where a
    host gives ``allow`` and ``continue`` different wire semantics.
    """

    if normaliser is None:
        return verdict
    normalised = normaliser(verdict, event)
    if not isinstance(normalised, HookVerdict):
        raise TypeError("verdict normaliser must return HookVerdict")
    return normalised


def _policy_id(policy: Mapping[str, Any], fallback: str = "") -> str:
    value = policy.get("policy_id", policy.get("id", fallback))
    return value if isinstance(value, str) else str(value)


def _number(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    return default


def policy_order_key(policy: Mapping[str, Any]) -> tuple[int, int, str, str]:
    """Return the documented stable policy order key.

    Lower explicit ``order`` runs first; higher ``priority`` wins within that
    order; the stable policy ID and canonical policy body break all remaining
    ties.  Input enumeration order therefore cannot affect execution order.
    """

    return (
        _number(policy.get("order")),
        -_number(policy.get("priority")),
        _policy_id(policy),
        json.dumps(
            dict(policy),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
    )


def _matches_dimension(policy: Mapping[str, Any], key: str, requested: str | None) -> bool:
    if requested is None or key not in policy:
        return True
    configured = policy[key]
    if isinstance(configured, str):
        return configured == requested or configured == "*"
    if isinstance(configured, (list, tuple, set, frozenset)):
        return requested in configured or "*" in configured
    return False


def select_policies(
    policies: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    *,
    event: str | None = None,
    harness: str | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Filter and stably order registry-like policies.

    A mapping registry may keep its ID in the mapping key; the returned copied
    policy then receives that value as ``policy_id`` when no explicit ID is
    present.  Optional ``events`` and ``harnesses`` dimensions support either
    a string, a collection, or ``"*"``.
    """

    materialised: list[dict[str, Any]] = []
    if isinstance(policies, Mapping):
        source = policies.items()
    else:
        source = ((str(index), policy) for index, policy in enumerate(policies))
    for fallback_id, policy in source:
        if not isinstance(policy, Mapping):
            raise TypeError("each policy must be a mapping")
        copied = dict(policy)
        copied.setdefault("policy_id", _policy_id(copied, fallback_id))
        if not _matches_dimension(copied, "events", event):
            continue
        if not _matches_dimension(copied, "harnesses", harness):
            continue
        materialised.append(copied)
    return tuple(MappingProxyType(policy) for policy in sorted(materialised, key=policy_order_key))


def verdict_order_key(verdict: HookVerdict) -> tuple[int, int, str, str]:
    """Return a stable winner key for verdict aggregation."""

    body = {
        "additional_context": verdict.additional_context,
        "audit_reference": verdict.audit_reference,
        "disposition": verdict.disposition,
        "evidence": verdict.to_dict()["evidence"],
        "failure_mode": verdict.failure_mode,
        "policy_id": verdict.policy_id,
        "reason": verdict.reason,
        "severity": verdict.severity,
        "system_message": verdict.system_message,
        "updated_input": verdict.to_dict()["updated_input"],
    }
    return (
        -_DISPOSITION_RANK[verdict.disposition],
        -_SEVERITY_RANK[verdict.severity],
        verdict.policy_id,
        json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
    )


def aggregate_verdicts(
    verdicts: Iterable[HookVerdict],
    *,
    event: str | None = None,
    normaliser: Callable[[HookVerdict, str | None], HookVerdict] | None = None,
) -> HookVerdict:
    """Select one deterministic verdict with deny precedence.

    This is monotone in disposition rank: adding an ``allow`` or ``continue``
    can never rescue a denial.  Equal-rank outcomes are resolved by severity,
    stable policy ID, and finally the canonical verdict body.
    """

    candidates = tuple(normalise_disposition(verdict, event, normaliser=normaliser) for verdict in verdicts)
    if not candidates:
        return HookVerdict(
            disposition="continue",
            reason="No policy produced a verdict",
            policy_id="policy.engine.no-match",
        )
    if any(not isinstance(verdict, HookVerdict) for verdict in candidates):
        raise TypeError("aggregate_verdicts accepts HookVerdict values only")
    return min(candidates, key=verdict_order_key)


def with_audit_reference(verdict: HookVerdict, reference: str) -> HookVerdict:
    """Return a new immutable verdict linked to an audit receipt."""

    return replace(verdict, audit_reference=reference)
