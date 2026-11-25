"""AGORA falsification harness — programmatic checks against the 6 spec conditions.

The protocol is wrong if any of these fire on a corpus of conversations:
1. Babel: median > 20 turns OR no termination >50% of cases
2. Consensus collapse: STOP-on-first-turn rate > 70%
3. Defer cascades: defer chains > 3 levels in > 10% (W4: tracked at runtime;
   actual measurement requires defer routing — deferred to W5)
4. Persona-off worse: A/B test (deferred — needs persona-on/off variants)
5. Cost blowup: median converged-cost > $0.10 (deferred — needs token telemetry, W6)
6. Single-agent equivalence: 5-agent quality matches 1-agent >80% (deferred — needs quality rubric)

W4 implements the falsifiers that need only structural data (1, 2). W5+W6
extend with cost and quality data once telemetry is wired.
"""
from __future__ import annotations

from dataclasses import dataclass

from lib.agora.telemetry import TelemetryAggregate, TelemetryRecord


@dataclass
class FalsifierVerdict:
    name: str
    failed: bool
    evidence: str


def check_babel(agg: TelemetryAggregate) -> FalsifierVerdict:
    """Babel: median > 20 turns OR no termination > 50% of cases."""
    no_term_rate = 1.0 - agg.convergence_rate()
    failed = agg.median_turns > 20 or no_term_rate > 0.5
    return FalsifierVerdict(
        name="babel",
        failed=failed,
        evidence=f"median_turns={agg.median_turns:.1f}, no_term_rate={no_term_rate:.2%}",
    )


def check_consensus_collapse(records: list[TelemetryRecord]) -> FalsifierVerdict:
    """Consensus collapse: STOP-on-first-turn rate > 70% across runs.

    A "first-turn stop" means quorum was reached at turn count <= N (where N is
    the agent count) — every agent stopped on their first response.
    """
    if not records:
        return FalsifierVerdict(name="consensus_collapse", failed=False, evidence="no records")
    first_turn_stops = sum(
        1 for r in records
        if r.converged() and r.turns <= r.agent_count
    )
    rate = first_turn_stops / len(records)
    return FalsifierVerdict(
        name="consensus_collapse",
        failed=rate > 0.7,
        evidence=f"first_turn_stop_rate={rate:.2%} ({first_turn_stops}/{len(records)})",
    )


def run_all(records: list[TelemetryRecord]) -> list[FalsifierVerdict]:
    """Run every implemented falsifier; return verdicts. Caller exits non-zero on any failure."""
    agg = TelemetryAggregate.from_records(records)
    return [
        check_babel(agg),
        check_consensus_collapse(records),
    ]
