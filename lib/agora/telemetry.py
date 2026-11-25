"""AGORA telemetry — per-conversation counters + multi-run aggregation.

W4: Used by the falsification harness to verify the 6 conditions from the
spec across N runs. W6 will extend this with cost telemetry (token counts
via chat_fn instrumentation) and conversation persistence.

Pure stdlib — no observability vendor coupling. Aggregates are computed
in-process; export to Grafana/Prometheus is W6+ scope.
"""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field

from lib.agora.runner import RunResult


@dataclass
class TelemetryRecord:
    """Per-conversation snapshot. Built from a RunResult."""

    convo_id: str
    turns: int
    elapsed_sec: float
    terminated_reason: str
    agent_count: int
    intervention_count: int = 0
    interventions_by_supervisor: dict[str, int] = field(default_factory=dict)

    def converged(self) -> bool:
        """Did the conversation terminate by genuine quorum (not cap or error)?"""
        return self.terminated_reason == "quorum_stop"

    @classmethod
    def from_result(cls, result: RunResult, agent_count: int) -> "TelemetryRecord":
        by_sup = Counter(i.supervisor for i in result.interventions)
        return cls(
            convo_id=result.convo_id,
            turns=result.turns,
            elapsed_sec=result.elapsed_sec,
            terminated_reason=result.terminated_reason,
            agent_count=agent_count,
            intervention_count=len(result.interventions),
            interventions_by_supervisor=dict(by_sup),
        )


@dataclass
class TelemetryAggregate:
    """Multi-run aggregate. Used by the falsification harness."""

    runs: int = 0
    converged_runs: int = 0
    median_turns: float = 0.0
    mean_elapsed: float = 0.0
    total_interventions: int = 0
    interventions_by_supervisor: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_records(cls, records: list[TelemetryRecord]) -> "TelemetryAggregate":
        if not records:
            return cls()
        sup_total: Counter[str] = Counter()
        for r in records:
            sup_total.update(r.interventions_by_supervisor)
        return cls(
            runs=len(records),
            converged_runs=sum(1 for r in records if r.converged()),
            median_turns=statistics.median(r.turns for r in records),
            mean_elapsed=statistics.fmean(r.elapsed_sec for r in records),
            total_interventions=sum(r.intervention_count for r in records),
            interventions_by_supervisor=dict(sup_total),
        )

    def convergence_rate(self) -> float:
        return self.converged_runs / self.runs if self.runs > 0 else 0.0
