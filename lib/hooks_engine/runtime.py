"""Single in-process seam from native payload to host-valid response."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .adapters import adapt
from .engine import EngineResult, PolicyEvaluator, evaluate
from .events import EventName, Harness, HookEvent
from .renderers import render


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    """Canonical evaluation plus the response for its originating harness."""

    event: HookEvent
    engine: EngineResult
    response: Mapping[str, Any]


def process(
    harness: str | Harness,
    payload: object,
    policies: Mapping[str, Mapping[str, Any]],
    evaluators: Mapping[str, PolicyEvaluator],
    *,
    event: str | EventName | None = None,
    audit_metadata: Mapping[str, Any] | None = None,
) -> RuntimeResult:
    """Adapt, evaluate, and render one hook call without host-specific gate code."""

    canonical = adapt(harness, payload, event)
    engine_result = evaluate(
        canonical,
        policies,
        evaluators,
        audit_metadata=audit_metadata,
    )
    response = render(
        canonical.harness,
        canonical.event,
        engine_result.final_verdict,
    )
    return RuntimeResult(
        event=canonical,
        engine=engine_result,
        response=response,
    )
