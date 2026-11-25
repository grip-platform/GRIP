#!/usr/bin/env python3
"""RSI data models — Candidate, ConvergenceState."""

from dataclasses import dataclass, field
from typing import Optional

from .config import DEFAULT_MERGE_THRESHOLD, CONVERGENCE_STALL_COUNT


@dataclass
class Candidate:
    """An improvement candidate for the RSI queue."""
    id: str
    title: str
    source: str  # "memory", "ralph", "pattern", "issue"
    impact: int = 3  # 1-5
    urgency: int = 3  # 1-5
    effort: int = 3  # 1-5
    estimated_cost_usd: float = 0.70
    issue_number: Optional[int] = None
    memory_item: Optional[int] = None
    ralph_feedback: Optional[str] = None
    status: str = "pending"  # pending, in_progress, completed, skipped
    skip_reason: Optional[str] = None
    is_epic: bool = False  # True for overhaul/epic issues requiring decomposition
    labels: list = field(default_factory=list)  # GitHub labels
    parent_epic_id: Optional[str] = None  # Links subtask to parent epic

    @property
    def priority_score(self) -> float:
        """Higher is better. Impact * Urgency / Effort."""
        if self.effort == 0:
            return 0.0
        return (self.impact * self.urgency) / self.effort


@dataclass
class ConvergenceState:
    """Tracks Ralph score trends for stall detection."""
    recent_scores: list = field(default_factory=list)
    stall_count: int = 0

    def record_score(self, score: int):
        self.recent_scores.append(score)
        # Keep last 10
        if len(self.recent_scores) > 10:
            self.recent_scores = self.recent_scores[-10:]
        # Check for stall: last N scores all in 70-84 range
        if len(self.recent_scores) >= CONVERGENCE_STALL_COUNT:
            recent = self.recent_scores[-CONVERGENCE_STALL_COUNT:]
            if all(70 <= s < DEFAULT_MERGE_THRESHOLD for s in recent):
                self.stall_count += 1
                return True
        return False

    @property
    def is_stalled(self) -> bool:
        if len(self.recent_scores) < CONVERGENCE_STALL_COUNT:
            return False
        recent = self.recent_scores[-CONVERGENCE_STALL_COUNT:]
        return all(70 <= s < DEFAULT_MERGE_THRESHOLD for s in recent)

    @property
    def has_scores(self) -> bool:
        """Whether avg_score reflects real reviews rather than the empty default.

        avg_score returns 0.0 for an empty history, which is indistinguishable
        from reviews that genuinely averaged zero. Callers that GRADE the value
        must consult this first: "no reviews recorded yet" is not "reviews scored
        zero", and treating it as the latter forfeits points for the absence of
        evidence rather than for evidence of a problem.

        avg_score keeps returning a float, because its other callers format it
        with :.1f and serialise it to JSON; this is the additive way to tell the
        two states apart without breaking them.
        """
        return bool(self.recent_scores)

    @property
    def avg_score(self) -> float:
        if not self.recent_scores:
            return 0.0
        return sum(self.recent_scores) / len(self.recent_scores)
