#!/usr/bin/env python3
"""
attest.py — Attestation-based convergence criterion for GRIP.

PROBLEM WITH SIMPLE THRESHOLDS:
    criterion = lambda state: state.score >= 80

This is gameable. Inflate one metric, pass the gate, regress later.
It also ignores history — a state that scored 81 for the first time
is treated identically to one that has scored 85+ across 10 iterations.

THE KONO INSIGHT:
    KONO's permanence pipeline promotes memories based on repeated
    confirmation, not recency. A memory seen once is provisional.
    A memory confirmed 3+ times across sessions becomes permanent.

THIS IS THE RIGHT MODEL FOR CONVERGENCE CRITERIA.

ATTESTATION-BASED CRITERION:
    A state "converges" only when multiple INDEPENDENT signals attest to it.
    Each signal has a confidence weight. The criterion is True when the
    weighted sum of attesting signals exceeds the confidence threshold.

BROLY POWER LEVELS FOR CRITERION EVALUATION:
    Level 0 (Thread)    : Fast heuristic — threshold check only
    Level 1 (Fork)      : Multi-signal — threshold + structural checks
    Level 2 (Council)   : Historical — requires N consistent observations
    Level 3 (Pipeline)  : Cross-session — KONO attestation required
    Level 4 (MESH)      : Independent validator agreement (council)

The criterion escalates through levels automatically. A state that passes
Level 0 but fails Level 1 is not yet converged — it's provisionally passing.

USAGE:
    from lib.attest import AttestCriterion

    criterion = AttestCriterion(
        threshold=0.80,          # 80% of weighted signals must agree
        min_level=2,             # Require at least Council-level attestation
        history_window=5,        # Look at last 5 states for consistency
    )

    # Pass to converge()
    result = converge(
        state=repo_health,
        criterion=criterion,
        improve=improve_repo,
        max_depth=3,
        _scorer=criterion.score,  # Use attestation confidence as scorer
    )
"""

from __future__ import annotations

import dataclasses
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """An independent attestation signal.

    name        : Human-readable label for this signal.
    check       : callable(state) → bool
    weight      : Contribution to overall confidence (0.0–1.0, must sum to 1.0).
    level       : Minimum Broly power level to evaluate this signal.
    description : Why this signal exists (prevents Goodhart gaming).
    """
    name: str
    check: Callable[[Any], bool]
    weight: float
    level: int = 0
    description: str = ""

    def evaluate(self, state: Any) -> tuple[bool, float]:
        """Returns (passed, weighted_contribution)."""
        try:
            passed = bool(self.check(state))
        except Exception as e:  # noqa: BLE001
            logger.warning("[attest] Signal '%s' raised: %s", self.name, e)
            passed = False
        return passed, self.weight if passed else 0.0


@dataclass
class Attestation:
    """Result of evaluating all signals against a state.

    confidence    : Weighted sum of passing signals (0.0–1.0).
    level_reached : Highest Broly level evaluated.
    passed        : True if confidence >= threshold.
    signals       : Dict of signal_name → (passed, contribution).
    verdict       : Human-readable summary.
    """
    confidence: float
    level_reached: int
    passed: bool
    signals: dict = field(default_factory=dict)
    verdict: str = ""


class AttestCriterion:
    """Attestation-based convergence criterion.

    Replaces simple threshold checks with multi-signal, history-aware,
    KONO-inspired attestation. A state converges only when independent
    signals agree with sufficient confidence.

    The criterion itself improves over time: observed states are stored
    in an internal history. The Level 2 (Council) check requires that the
    last N states show a monotonic improvement trend — preventing one-shot
    lucky passes.

    Design: No external state storage required. Pass an instance to
    converge() and reuse across sessions by pickling or by re-constructing
    with the same signals.
    """

    def __init__(
        self,
        signals: Optional[list[Signal]] = None,
        threshold: float = 0.75,
        min_level: int = 1,
        history_window: int = 3,
    ):
        """
        Args:
            signals        : List of Signal objects. If None, uses defaults.
            threshold      : Minimum confidence to pass (0.0–1.0).
            min_level      : Minimum Broly level before criterion can pass.
                             Level 0 = any single signal.
                             Level 1 = structural multi-signal check.
                             Level 2 = requires consistent history.
            history_window : For Level 2: require this many consistent states.
        """
        self.signals = signals or []
        self.threshold = threshold
        self.min_level = min_level
        self.history_window = history_window
        self._history: list[tuple[Any, float]] = []  # (state, confidence)

        # Validate weights sum to ~1.0
        if self.signals:
            total_weight = sum(s.weight for s in self.signals)
            if not (0.95 <= total_weight <= 1.05):
                logger.warning(
                    "[attest] Signal weights sum to %.2f (should be 1.0). "
                    "Normalising.", total_weight
                )
                # Create new Signal copies — never mutate caller's objects.
                self.signals = [
                    dataclasses.replace(s, weight=s.weight / total_weight)
                    for s in self.signals
                ]

    def __call__(self, state: Any) -> bool:
        """Make AttestCriterion callable — pass directly to converge()."""
        return self.attest(state).passed

    def score(self, state: Any) -> float:
        """Return confidence as a float score (0.0–1.0) for use as _scorer."""
        return self.attest(state).confidence

    def attest(self, state: Any) -> Attestation:
        """Evaluate all signals against state and return attestation.

        Evaluation levels:
            Level 0: Evaluate all Level-0 signals only (fast heuristic).
            Level 1: Evaluate all Level 0+1 signals (full structural check).
            Level 2: Add historical consistency check (last N states trending up).
            Level 3+: Reserved for KONO cross-session attestation.
        """
        if not self.signals:
            logger.warning("[attest] No signals defined — criterion always False")
            return Attestation(confidence=0.0, level_reached=-1, passed=False,
                               verdict="No signals defined")

        # Determine evaluation level based on history depth
        level = min(2, len(self._history))
        level = max(level, self.min_level)

        # Evaluate signals up to this level
        signal_results = {}
        confidence = 0.0
        for sig in self.signals:
            if sig.level <= level:
                passed, contribution = sig.evaluate(state)
                signal_results[sig.name] = {"passed": passed, "contribution": contribution,
                                             "weight": sig.weight}
                confidence += contribution

        # Level 2: Historical consistency gate (requires monotonic trend)
        history_ok = True
        if level >= 2 and len(self._history) >= self.history_window:
            recent_confidences = [c for _, c in self._history[-self.history_window:]]
            # Monotonic increase required: each confidence >= previous
            history_ok = all(
                recent_confidences[i] <= recent_confidences[i + 1]
                for i in range(len(recent_confidences) - 1)
            )
            if not history_ok:
                logger.debug(
                    "[attest] Level 2 history check failed: %s", recent_confidences
                )

        passed = (
            confidence >= self.threshold
            and level >= self.min_level
            and history_ok
        )

        verdict = self._format_verdict(confidence, level, history_ok, signal_results)

        # Record this evaluation in history
        self._history.append((state, confidence))
        if len(self._history) > 20:  # Cap history to prevent unbounded growth
            self._history = self._history[-20:]

        return Attestation(
            confidence=confidence,
            level_reached=level,
            passed=passed,
            signals=signal_results,
            verdict=verdict,
        )

    def _format_verdict(self, confidence: float, level: int,
                        history_ok: bool, signal_results: dict) -> str:
        """Human-readable attestation summary."""
        passing_signals = [k for k, v in signal_results.items() if v["passed"]]
        failing_signals = [k for k, v in signal_results.items() if not v["passed"]]

        parts = [f"confidence={confidence:.2f}/{self.threshold:.2f}"]
        parts.append(f"level={level}/{self.min_level}")
        if failing_signals:
            parts.append(f"failing=[{', '.join(failing_signals)}]")
        if not history_ok:
            parts.append("history=non-monotonic")
        return " | ".join(parts)

    def reset_history(self):
        """Clear evaluation history. Useful for starting a fresh convergence run."""
        self._history.clear()


# ── Factory functions for common use cases ────────────────────────────────

def pr_score_criterion(pass_score: int = 80) -> AttestCriterion:
    """Pre-built attestation criterion for PR pre-review pipeline.

    Replaces the simple `score >= 80` check in PreReviewPipeline.
    Adds structural signals: requires testing score to be non-zero and
    alignment score to not be a pure echo of quality score (Goodhart guard).
    """
    signals = [
        Signal(
            name="composite_score",
            check=lambda s: s.get("composite_score", 0) >= pass_score
                if isinstance(s, dict) else s.composite_score >= pass_score,
            weight=0.60,
            level=0,
            description=f"Composite PR score >= {pass_score}",
        ),
        Signal(
            name="testing_nonzero",
            check=lambda s: (
                s.get("dimension_scores", {}).get("testing", 0) > 0
                if isinstance(s, dict)
                else s.dimension_scores.testing > 0
            ),
            weight=0.25,
            level=0,
            description="Testing score is non-zero (test files were changed)",
        ),
        Signal(
            name="not_all_equal",
            check=lambda s: len(set((
                s.get("dimension_scores", {}).get("quality", 0),
                s.get("dimension_scores", {}).get("alignment", 0),
                s.get("dimension_scores", {}).get("testing", 0),
                s.get("dimension_scores", {}).get("docs", 0),
            ))) > 1 if isinstance(s, dict) else True,
            weight=0.15,
            level=1,
            description=(
                "Dimensions are not all identical — Goodhart guard against "
                "a scorer returning the same value for every dimension"
            ),
        ),
    ]

    return AttestCriterion(
        signals=signals,
        threshold=0.75,
        min_level=0,  # PR scoring is single-shot, no history required
        history_window=1,
    )
