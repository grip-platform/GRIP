"""
consensus.py — Dual consensus model for Broly council decisions (#220, #221).

Two types of consensus, reflecting the nature of the assertion:

1. FACTUAL consensus: Observable facts (code exists, test passes, metric value).
   Requires UNANIMOUS agreement — if agents disagree on facts, something is wrong.

2. STRATEGIC consensus: Recommendations, architectural choices, trade-offs.
   Requires SUPERMAJORITY (configurable, default 4/6 for council, 4/N for cluster).

The consensus model itself is a convergence function:
    converge(opinions, all_agree, refine_opinions) → decision

Usage:
    from lib.broly.consensus import Council, Assertion, ConsensusType

    council = Council(agents=["pragmatist", "theorist", "security", "ux", "devil", "integrator"])

    # Factual assertion — needs unanimity
    result = council.vote(Assertion(
        claim="tests pass on main branch",
        assertion_type=ConsensusType.FACTUAL,
        evidence={"ci_status": "green"},
    ))

    # Strategic assertion — needs supermajority
    result = council.vote(Assertion(
        claim="use Redis for caching layer",
        assertion_type=ConsensusType.STRATEGIC,
    ))
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ConsensusType(Enum):
    """Type of assertion determines consensus threshold."""
    FACTUAL = "factual"        # Observable truth — unanimous
    STRATEGIC = "strategic"    # Recommendation — supermajority


class VoteValue(Enum):
    """Agent's position on an assertion."""
    AGREE = "agree"
    DISAGREE = "disagree"
    ABSTAIN = "abstain"


@dataclass
class Vote:
    """A single agent's vote with reasoning."""
    agent: str
    value: VoteValue
    reasoning: str = ""
    confidence: float = 0.5  # 0-1


@dataclass
class Assertion:
    """A claim to be evaluated by the council."""
    claim: str
    assertion_type: ConsensusType = ConsensusType.STRATEGIC
    evidence: dict = field(default_factory=dict)
    context: str = ""


@dataclass
class ConsensusResult:
    """Outcome of a council vote."""
    assertion: Assertion
    passed: bool
    votes_for: int
    votes_against: int
    votes_abstain: int
    total_agents: int
    threshold_required: float
    threshold_achieved: float
    dissent: list[Vote] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    @property
    def unanimous(self) -> bool:
        return self.votes_against == 0 and self.votes_abstain == 0

    def summary(self) -> str:
        status = "PASSED" if self.passed else "REJECTED"
        return (
            f"[{status}] {self.assertion.claim} "
            f"({self.votes_for}/{self.total_agents}, "
            f"need {self.threshold_required:.0%})"
        )


class Council:
    """Dual consensus council for Broly decisions.

    Factual assertions require unanimity.
    Strategic assertions require supermajority (configurable).
    """

    def __init__(
        self,
        agents: list[str],
        strategic_threshold: float = 0.67,
        cluster_quorum: int = 4,
    ):
        self.agents = agents
        self.strategic_threshold = strategic_threshold
        self.cluster_quorum = cluster_quorum
        self.history: list[ConsensusResult] = []

    def vote(self, assertion: Assertion, votes: list[Vote]) -> ConsensusResult:
        """Evaluate votes against the appropriate consensus threshold.

        Args:
            assertion: The claim being voted on.
            votes: List of Vote objects from council agents.

        Returns:
            ConsensusResult with pass/fail and dissent details.
        """
        # Count votes
        votes_for = sum(1 for v in votes if v.value == VoteValue.AGREE)
        votes_against = sum(1 for v in votes if v.value == VoteValue.DISAGREE)
        votes_abstain = sum(1 for v in votes if v.value == VoteValue.ABSTAIN)
        total = len(votes)

        # Determine threshold based on assertion type
        if assertion.assertion_type == ConsensusType.FACTUAL:
            threshold = 1.0  # Unanimous
        else:
            threshold = self.strategic_threshold

        # Calculate achieved ratio (abstentions don't count against)
        participating = votes_for + votes_against
        achieved = votes_for / max(participating, 1)

        passed = achieved >= threshold

        # Collect dissent
        dissent = [v for v in votes if v.value == VoteValue.DISAGREE]

        result = ConsensusResult(
            assertion=assertion,
            passed=passed,
            votes_for=votes_for,
            votes_against=votes_against,
            votes_abstain=votes_abstain,
            total_agents=total,
            threshold_required=threshold,
            threshold_achieved=achieved,
            dissent=dissent,
        )

        self.history.append(result)
        return result

    def check_quorum(self, votes: list[Vote]) -> bool:
        """Check if minimum quorum of agents participated (#221).

        For CLUSTER power level, at least cluster_quorum agents
        must have voted (not abstained) for the result to be valid.
        """
        participating = sum(1 for v in votes if v.value != VoteValue.ABSTAIN)
        return participating >= self.cluster_quorum

    def factual(self, claim: str, evidence: Optional[dict] = None) -> Assertion:
        """Create a factual assertion (convenience method)."""
        return Assertion(
            claim=claim,
            assertion_type=ConsensusType.FACTUAL,
            evidence=evidence or {},
        )

    def strategic(self, claim: str, context: str = "") -> Assertion:
        """Create a strategic assertion (convenience method)."""
        return Assertion(
            claim=claim,
            assertion_type=ConsensusType.STRATEGIC,
            context=context,
        )

    def audit_log(self) -> list[dict]:
        """Return the full decision history as dicts."""
        return [
            {
                "claim": r.assertion.claim,
                "type": r.assertion.assertion_type.value,
                "passed": r.passed,
                "votes": f"{r.votes_for}/{r.total_agents}",
                "threshold": f"{r.threshold_required:.0%}",
                "achieved": f"{r.threshold_achieved:.0%}",
                "dissent_count": len(r.dissent),
                "timestamp": r.timestamp,
            }
            for r in self.history
        ]
