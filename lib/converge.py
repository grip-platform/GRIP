#!/usr/bin/env python3
"""
converge.py — Universal convergence kernel for GRIP.

Every GRIP recursive system is an instance of this pattern:

    converge(pr_diff, score_ok, apply_ralph_feedback)   → PreReviewPipeline
    converge(open_issue, is_fixed, sprint_loop_step)    → RSI loop
    converge(repo_state, quality_gate, mirror_improve)  → GRIP MIRROR

The kernel separates concerns cleanly:
  - state      : what we're improving (any hashable or serialisable value)
  - criterion  : callable(state) → bool — stop when True
  - improve    : callable(state) → state — one improvement cycle
  - max_depth  : safety ceiling, prevents infinite recursion

All iterations are tracked. The best state seen is returned if criterion
is never fully satisfied (graceful degradation over hard failure).

Usage:
    from lib.converge import converge, ConvergenceResult

    result = converge(
        state={"score": 52, "pr": 198},
        criterion=lambda s: s["score"] >= 90,
        improve=apply_ralph_fix,
        max_depth=3,
        label="pr-198-improvement",
    )
    print(result.best_state, result.iterations)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ConvergenceResult:
    """Outcome of a converge() call.

    best_state     : highest-quality state observed across all iterations
    final_state    : last state reached (may equal best_state)
    satisfied      : True if criterion was met before max_depth
    iterations     : number of improve() calls made
    depth_reached  : recursion depth when convergence stopped
    history        : full list of (depth, state) pairs for transparency
    elapsed_sec    : wall-clock time for the full convergence run
    label          : optional identifier for logging / KONO storage
    """
    best_state: Any
    final_state: Any
    satisfied: bool
    iterations: int
    depth_reached: int
    history: list[tuple[int, Any]] = field(default_factory=list)
    elapsed_sec: float = 0.0
    label: str = ""


def converge(
    state: Any,
    criterion: Callable[[Any], bool],
    improve: Callable[[Any], Any],
    depth: int = 0,
    max_depth: int = 3,
    _history: Optional[list] = None,
    _best: Optional[list] = None,
    _scorer: Optional[Callable[[Any], float]] = None,
    _start: Optional[float] = None,
    label: str = "",
    time_budget_sec: Optional[float] = None,
) -> ConvergenceResult:
    """Universal convergence function — the recursive heart of GRIP.

    Applies improve(state) repeatedly until criterion(state) is True,
    max_depth is reached, or time_budget_sec is exceeded. Tracks the best
    state seen using _scorer if provided, otherwise treats the last state
    as best.

    Args:
        state      : Current state to evaluate and improve.
        criterion  : Returns True when state is good enough to stop.
        improve    : Returns an improved state. Must be pure or idempotent.
        depth      : Current recursion depth (managed internally).
        max_depth  : Hard ceiling on recursion. Default 3.
        _history   : Accumulated history (managed internally).
        _best      : [best_state, best_score] mutable cell (internal).
        _scorer    : Optional callable(state) → float for best-state tracking.
                     If absent, each new state replaces best (recency wins).
        _start     : Epoch time at root call (internal).
        label      : Human-readable identifier for logging and KONO.
        time_budget_sec : Optional wall-clock budget in seconds. When elapsed
                     time exceeds this, return best state (Pareto-efficient).
                     Inspired by Karpathy autoresearch fixed-budget pattern.

    Returns:
        ConvergenceResult with best_state, history, and convergence metadata.
    """
    # Initialise mutable state on root call
    if _history is None:
        _history = []
    if _start is None:
        _start = time.monotonic()
    if _best is None:
        score = _scorer(state) if _scorer else float('-inf')
        _best = [state, score]

    _history.append((depth, state))

    # Update best-state tracker
    # When no scorer, use 0.0 constant + >= so the most recent state wins (recency semantics).
    # float(depth) was wrong: it conflated "deeper" with "higher quality".
    current_score = _scorer(state) if _scorer else 0.0
    if current_score >= _best[1]:
        _best[0] = state
        _best[1] = current_score

    # Base case: criterion satisfied, depth ceiling, or time budget exceeded
    elapsed = time.monotonic() - _start
    time_exceeded = time_budget_sec is not None and elapsed >= time_budget_sec
    if criterion(state) or depth >= max_depth or time_exceeded:
        satisfied = criterion(state)
        if satisfied:
            logger.info("[converge] %s satisfied at depth=%d", label or "unlabelled", depth)
        elif time_exceeded:
            logger.warning(
                "[converge] %s hit time_budget=%.1fs at depth=%d (elapsed=%.1fs)",
                label or "unlabelled", time_budget_sec, depth, elapsed,
            )
        else:
            logger.warning(
                "[converge] %s hit max_depth=%d without satisfying criterion",
                label or "unlabelled", max_depth,
            )
        return ConvergenceResult(
            best_state=_best[0],
            final_state=state,
            satisfied=satisfied,
            iterations=depth,
            depth_reached=depth,
            history=list(_history),
            elapsed_sec=elapsed,
            label=label,
        )

    # Recursive case: improve and recurse
    logger.debug("[converge] %s depth=%d → improving", label or "unlabelled", depth)
    next_state = improve(state)

    return converge(
        state=next_state,
        criterion=criterion,
        improve=improve,
        depth=depth + 1,
        max_depth=max_depth,
        _history=_history,
        _best=_best,
        _scorer=_scorer,
        _start=_start,
        label=label,
        time_budget_sec=time_budget_sec,
    )


def converge_iter(
    state: Any,
    criterion: Callable[[Any], bool],
    improve: Callable[[Any], Any],
    max_depth: int = 3,
    _scorer: Optional[Callable[[Any], float]] = None,
    label: str = "",
    time_budget_sec: Optional[float] = None,
) -> ConvergenceResult:
    """Iterative variant of converge() for environments that limit recursion depth.

    Functionally identical to converge() but implemented as a loop.
    Prefer this for max_depth > 50.
    """
    history: list[tuple[int, Any]] = []
    start = time.monotonic()
    best_state = state
    best_score = _scorer(state) if _scorer else float('-inf')

    for depth in range(max_depth + 1):
        history.append((depth, state))
        current_score = _scorer(state) if _scorer else 0.0
        if current_score >= best_score:
            best_state = state
            best_score = current_score

        elapsed = time.monotonic() - start
        time_exceeded = time_budget_sec is not None and elapsed >= time_budget_sec

        if criterion(state) or time_exceeded:
            satisfied = criterion(state)
            if time_exceeded and not satisfied:
                logger.warning(
                    "[converge_iter] %s hit time_budget=%.1fs at depth=%d",
                    label or "unlabelled", time_budget_sec, depth,
                )
            return ConvergenceResult(
                best_state=best_state,
                final_state=state,
                satisfied=satisfied,
                iterations=depth,
                depth_reached=depth,
                history=history,
                elapsed_sec=elapsed,
                label=label,
            )

        if depth < max_depth:
            state = improve(state)

    return ConvergenceResult(
        best_state=best_state,
        final_state=state,
        satisfied=False,
        iterations=max_depth,
        depth_reached=max_depth,
        history=history,
        elapsed_sec=time.monotonic() - start,
        label=label,
    )


def converge_accumulate(
    state: Any,
    criterion: Callable[[Any], bool],
    improve: Callable[[Any], Any],
    merge: Callable[[Any, Any], Any],
    max_depth: int = 3,
    _scorer: Optional[Callable[[Any], float]] = None,
    label: str = "",
    time_budget_sec: Optional[float] = None,
) -> ConvergenceResult:
    """Accumulative convergence — RLM answer pattern.

    Unlike converge() where improve() replaces state entirely, this variant
    uses a merge function to accumulate improvements into state. Each iteration
    produces a delta, and merge(state, delta) builds the answer progressively.

    Inspired by RLM (Recursive Language Models, arXiv 2512.24601) where
    answer["content"] is written iteratively until answer["ready"] is True.

    Args:
        state      : Initial accumulator (e.g. {"content": "", "ready": False}).
        criterion  : Returns True when accumulated state is complete.
        improve    : Returns a delta/chunk to merge into state.
        merge      : Combines state + delta into new state.
        max_depth  : Hard ceiling on iterations.
        _scorer    : Optional quality scorer for best-state tracking.
        label      : Human-readable identifier.
        time_budget_sec : Optional wall-clock budget.

    Example:
        result = converge_accumulate(
            state={"content": "", "sections": [], "ready": False},
            criterion=lambda s: s["ready"],
            improve=lambda s: generate_next_section(s),
            merge=lambda s, d: {**s, "content": s["content"] + d["text"],
                                "sections": s["sections"] + [d],
                                "ready": d.get("final", False)},
            max_depth=10,
            label="document-builder",
        )
    """
    history: list[tuple[int, Any]] = []
    start = time.monotonic()
    best_state = state
    best_score = _scorer(state) if _scorer else float('-inf')

    for depth in range(max_depth + 1):
        history.append((depth, state))
        current_score = _scorer(state) if _scorer else 0.0
        if current_score >= best_score:
            best_state = state
            best_score = current_score

        elapsed = time.monotonic() - start
        time_exceeded = time_budget_sec is not None and elapsed >= time_budget_sec

        if criterion(state) or time_exceeded:
            satisfied = criterion(state)
            if time_exceeded and not satisfied:
                logger.warning(
                    "[converge_accumulate] %s hit time_budget=%.1fs at depth=%d",
                    label or "unlabelled", time_budget_sec, depth,
                )
            return ConvergenceResult(
                best_state=best_state,
                final_state=state,
                satisfied=satisfied,
                iterations=depth,
                depth_reached=depth,
                history=history,
                elapsed_sec=elapsed,
                label=label,
            )

        if depth < max_depth:
            delta = improve(state)
            state = merge(state, delta)

    return ConvergenceResult(
        best_state=best_state,
        final_state=state,
        satisfied=False,
        iterations=max_depth,
        depth_reached=max_depth,
        history=history,
        elapsed_sec=time.monotonic() - start,
        label=label,
    )
