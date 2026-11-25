"""Deterministic execution of pure hook policies over canonical events."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from .audit import AuditReceipt, create_audit_receipt
from .events import HookEvent
from .verdicts import HookVerdict, aggregate_verdicts, select_policies, with_audit_reference

PolicyEvaluator = Callable[[HookEvent, Mapping[str, Any]], HookVerdict]


@dataclass(frozen=True, slots=True)
class EngineResult:
    """One replayable engine evaluation, including its redacted audit receipt."""

    event: HookEvent
    selected_policy_ids: tuple[str, ...]
    verdicts: tuple[HookVerdict, ...]
    final_verdict: HookVerdict
    audit: AuditReceipt


def _policy_failure(policy: Mapping[str, Any], error: Exception) -> HookVerdict:
    policy_id = str(policy["policy_id"])
    failure_mode = str(policy.get("failure_mode", "open"))
    severity = str(policy.get("classification", "advisory"))
    return HookVerdict(
        disposition="deny" if failure_mode == "closed" else "feedback",
        reason=f"{policy_id} failed during deterministic evaluation",
        policy_id=policy_id,
        severity=severity,
        failure_mode=failure_mode,
        evidence={"error_type": type(error).__name__},
    )


def _evaluate_policy(
    event: HookEvent,
    policy: Mapping[str, Any],
    evaluators: Mapping[str, PolicyEvaluator],
) -> HookVerdict:
    policy_id = str(policy["policy_id"])
    implementation = str(policy.get("implementation", policy_id))
    try:
        evaluator = evaluators[implementation]
        verdict = evaluator(event, policy)
        if not isinstance(verdict, HookVerdict):
            raise TypeError("policy evaluator must return HookVerdict")
        return replace(
            verdict,
            policy_id=policy_id,
            severity=str(policy.get("classification", verdict.severity)),
            failure_mode=str(policy.get("failure_mode", verdict.failure_mode)),
            audit_reference="",
        )
    except Exception as error:
        return _policy_failure(policy, error)


def evaluate(
    event: HookEvent,
    policies: Mapping[str, Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    evaluators: Mapping[str, PolicyEvaluator],
    *,
    audit_metadata: Mapping[str, Any] | None = None,
) -> EngineResult:
    """Select, execute, aggregate, and audit policies in one deterministic pass.

    Missing or malformed evaluators follow each policy's declared failure mode:
    closed policies deny; open policies return visible feedback. Registry
    classification and failure mode override evaluator-supplied metadata.
    """

    if not isinstance(event, HookEvent):
        raise TypeError("event must be HookEvent")
    selected = select_policies(
        policies,
        event=event.event.value,
        harness=event.harness.value,
    )
    verdicts = tuple(_evaluate_policy(event, policy, evaluators) for policy in selected)
    final_without_audit = aggregate_verdicts(verdicts, event=event.event.value)
    receipt = create_audit_receipt(
        event,
        verdicts,
        final_without_audit,
        metadata=audit_metadata,
    )
    final = with_audit_reference(final_without_audit, receipt.reference)
    return EngineResult(
        event=event,
        selected_policy_ids=tuple(str(policy["policy_id"]) for policy in selected),
        verdicts=verdicts,
        final_verdict=final,
        audit=receipt,
    )
