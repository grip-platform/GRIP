#!/usr/bin/env python3
"""
converge_combinators.py — Composable primitives for the convergence kernel.

The converge() kernel takes (state, criterion, improve). These combinators
let you BUILD complex criteria and improvers from simple, reusable parts:

    # Compose criteria: ALL must pass
    criterion = all_of(tests_pass, lint_clean, score_above(80))

    # Compose improvers: sequential pipeline
    improve = pipeline(fix_tests, fix_lint, improve_score)

    # Compose with fallback: try A, fall back to B
    improve = fallback(fast_fix, slow_fix)

    # Convergence with adaptive strategy selection
    result = converge(state, criterion, adaptive(strategies), max_depth=5)

This makes convergence DECLARATIVE — describe what you want, not how to get it.
Every combinator is itself composable, creating fractal convergence at the API level.

Usage:
    from lib.converge_combinators import all_of, pipeline, adaptive, converge_compose
    from lib.converge import converge

    result = converge(
        state={"tests": 80, "lint": 50, "score": 60},
        criterion=all_of(
            lambda s: s["tests"] >= 95,
            lambda s: s["lint"] >= 90,
            lambda s: s["score"] >= 85,
        ),
        improve=pipeline(
            lambda s: {**s, "tests": min(100, s["tests"] + 10)},
            lambda s: {**s, "lint": min(100, s["lint"] + 15)},
            lambda s: {**s, "score": min(100, s["score"] + 8)},
        ),
        max_depth=5,
    )
"""

from __future__ import annotations

from typing import Any, Callable, Optional


# =========================================================================
# Criterion Combinators — compose boolean convergence checks
# =========================================================================

def all_of(*criteria: Callable[[Any], bool]) -> Callable[[Any], bool]:
    """Require ALL criteria to pass. Short-circuits on first failure.

    This is the anti-Goodhart combinator: you can't game one metric
    at the expense of others. All signals must converge together.
    """
    def combined(state: Any) -> bool:
        return all(c(state) for c in criteria)
    combined.__name__ = f"all_of({len(criteria)})"
    return combined


def any_of(*criteria: Callable[[Any], bool]) -> Callable[[Any], bool]:
    """Require ANY criterion to pass. Short-circuits on first success.

    Use for graceful degradation: if the ideal isn't achievable,
    accept any of the acceptable alternatives.
    """
    def combined(state: Any) -> bool:
        return any(c(state) for c in criteria)
    combined.__name__ = f"any_of({len(criteria)})"
    return combined


def threshold(key: str, minimum: float) -> Callable[[Any], bool]:
    """Create a criterion that checks state[key] >= minimum.

    Shorthand for the most common convergence check pattern.
    """
    def check(state: Any) -> bool:
        if isinstance(state, dict):
            return state.get(key, 0) >= minimum
        return getattr(state, key, 0) >= minimum
    check.__name__ = f"threshold({key}>={minimum})"
    return check


def weighted_threshold(
    weights: dict[str, float],
    minimum: float,
) -> Callable[[Any], bool]:
    """Create a criterion that checks weighted average >= minimum.

    Mirrors the CUBE's composite scoring: different signals have
    different importance, but all contribute to the convergence decision.
    """
    total_weight = sum(weights.values())

    def check(state: Any) -> bool:
        if total_weight == 0:
            return True
        score = sum(
            (state.get(k, 0) if isinstance(state, dict) else getattr(state, k, 0)) * w
            for k, w in weights.items()
        ) / total_weight
        return score >= minimum
    check.__name__ = f"weighted_threshold(>={minimum})"
    return check


# =========================================================================
# Improve Combinators — compose state transformation strategies
# =========================================================================

def pipeline(*improvers: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Apply improvers sequentially. Each gets the output of the previous.

    This is function composition: pipeline(f, g, h)(x) = h(g(f(x))).
    The convergence equivalent of a Unix pipe.
    """
    def combined(state: Any) -> Any:
        for imp in improvers:
            state = imp(state)
        return state
    combined.__name__ = f"pipeline({len(improvers)})"
    return combined


def fallback(*improvers: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Try each improver in order, return first non-exception result.

    Graceful degradation for improvement strategies: if the optimal
    approach fails, automatically fall back to the next best.
    """
    def combined(state: Any) -> Any:
        for imp in improvers:
            try:
                result = imp(state)
                if result is not None:
                    return result
            except Exception:
                continue
        return state  # All failed — return unchanged
    combined.__name__ = f"fallback({len(improvers)})"
    return combined


def adaptive(
    strategies: list[Callable[[Any], Any]],
    scorer: Optional[Callable[[Any], float]] = None,
) -> Callable[[Any], Any]:
    """Select the best improvement strategy per iteration.

    Tries each strategy, scores the result, picks the best outcome.
    This is the convergence equivalent of beam search — explore multiple
    paths and keep the best.

    When no scorer is provided, uses the last strategy that doesn't raise.
    """
    def combined(state: Any) -> Any:
        best_state = state
        best_score = scorer(state) if scorer else float('-inf')

        for strategy in strategies:
            try:
                candidate = strategy(state)
                if scorer:
                    score = scorer(candidate)
                    if score > best_score:
                        best_state = candidate
                        best_score = score
                else:
                    best_state = candidate  # No scorer — last wins
            except Exception:
                continue

        return best_state
    combined.__name__ = f"adaptive({len(strategies)})"
    return combined


def focus(key: str, improver: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Apply an improver to a single key of a dict state.

    Isolates improvement to one domain without touching others.
    The convergence equivalent of single-responsibility principle.
    """
    def combined(state: Any) -> Any:
        if isinstance(state, dict):
            new_val = improver(state.get(key))
            return {**state, key: new_val}
        old_val = getattr(state, key, None)
        new_val = improver(old_val)
        # For dataclasses/objects, create a copy
        import copy
        new_state = copy.copy(state)
        setattr(new_state, key, new_val)
        return new_state
    combined.__name__ = f"focus({key})"
    return combined


# =========================================================================
# Scorer Combinators — compose quality measurement functions
# =========================================================================

def composite_scorer(
    weights: dict[str, float],
) -> Callable[[Any], float]:
    """Create a weighted composite scorer from state keys.

    Mirrors PrinciplesReport.composite_score but generalized to any state.
    """
    total_weight = sum(weights.values())

    def score(state: Any) -> float:
        if total_weight == 0:
            return 0.0
        return sum(
            (state.get(k, 0) if isinstance(state, dict) else getattr(state, k, 0)) * w
            for k, w in weights.items()
        ) / total_weight
    score.__name__ = f"composite_scorer({len(weights)})"
    return score


# =========================================================================
# Velocity Tracker — measure convergence rate across iterations
# =========================================================================

class VelocityTracker:
    """Track convergence velocity across iterations.

    Velocity = score change per iteration. Positive = improving.
    Acceleration = velocity change per iteration. Positive = accelerating.

    When velocity approaches zero, convergence is stalling — the system
    should switch strategies (via adaptive()) or increase max_depth.
    """

    def __init__(self, scorer: Callable[[Any], float]):
        self.scorer = scorer
        self.scores: list[float] = []

    def record(self, state: Any) -> None:
        """Record a state observation."""
        self.scores.append(self.scorer(state))

    @property
    def velocity(self) -> float:
        """Current rate of improvement (score change per step)."""
        if len(self.scores) < 2:
            return 0.0
        return self.scores[-1] - self.scores[-2]

    @property
    def acceleration(self) -> float:
        """Rate of velocity change (second derivative)."""
        if len(self.scores) < 3:
            return 0.0
        v1 = self.scores[-2] - self.scores[-3]
        v2 = self.scores[-1] - self.scores[-2]
        return v2 - v1

    @property
    def is_stalling(self) -> bool:
        """True if velocity is near zero or negative."""
        return len(self.scores) >= 3 and abs(self.velocity) < 0.01

    @property
    def is_accelerating(self) -> bool:
        """True if improvement rate is increasing."""
        return self.acceleration > 0.01

    def as_improve_wrapper(
        self,
        base_improve: Callable[[Any], Any],
        stall_strategy: Optional[Callable[[Any], Any]] = None,
    ) -> Callable[[Any], Any]:
        """Wrap an improve function with velocity tracking.

        If convergence stalls and a stall_strategy is provided,
        automatically switches to the alternative strategy.
        """
        def wrapped(state: Any) -> Any:
            self.record(state)
            if self.is_stalling and stall_strategy is not None:
                return stall_strategy(state)
            return base_improve(state)
        wrapped.__name__ = f"velocity_tracked({base_improve.__name__})"
        return wrapped

    def summary(self) -> dict:
        """Return velocity metrics as a dict."""
        return {
            "observations": len(self.scores),
            "velocity": round(self.velocity, 4),
            "acceleration": round(self.acceleration, 4),
            "is_stalling": self.is_stalling,
            "is_accelerating": self.is_accelerating,
            "trajectory": [round(s, 4) for s in self.scores[-5:]],
        }


# =========================================================================
# Convenience: converge_compose() — convergence with all combinators
# =========================================================================

def converge_compose(
    state: Any,
    criteria: list[Callable[[Any], bool]],
    improvers: list[Callable[[Any], Any]],
    scorer: Optional[Callable[[Any], float]] = None,
    max_depth: int = 3,
    label: str = "",
    track_velocity: bool = False,
) -> dict:
    """High-level convergence with combinators.

    Composes criteria with all_of, improvers with pipeline,
    optionally tracks velocity.

    Returns a dict with ConvergenceResult fields plus velocity data.
    """
    from lib.converge import converge

    criterion = all_of(*criteria) if len(criteria) > 1 else criteria[0]
    improve = pipeline(*improvers) if len(improvers) > 1 else improvers[0]

    tracker = None
    if track_velocity and scorer:
        tracker = VelocityTracker(scorer)
        improve = tracker.as_improve_wrapper(improve)

    result = converge(
        state=state,
        criterion=criterion,
        improve=improve,
        max_depth=max_depth,
        _scorer=scorer,
        label=label,
    )

    output = {
        "best_state": result.best_state,
        "final_state": result.final_state,
        "satisfied": result.satisfied,
        "iterations": result.iterations,
        "depth_reached": result.depth_reached,
        "elapsed_sec": result.elapsed_sec,
        "label": result.label,
    }

    if tracker:
        tracker.record(result.final_state)  # Record final state
        output["velocity"] = tracker.summary()

    return output
