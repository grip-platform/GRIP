#!/usr/bin/env python3
"""RSI convergence detection — score tracking, Ralph feed, stop signal."""

import json
from dataclasses import asdict
from datetime import datetime, timezone

from . import config
from .models import Candidate, ConvergenceState
from .metrics import record_metric


def load_convergence() -> ConvergenceState:
    """Load convergence tracking state."""
    state_file = config.CLAUDE_DIR / "state" / "rsi" / "rsi-convergence-state.json"
    if not state_file.exists():
        return ConvergenceState()
    try:
        data = json.loads(state_file.read_text())
        return ConvergenceState(
            recent_scores=data.get("recent_scores", []),
            stall_count=data.get("stall_count", 0),
        )
    except (json.JSONDecodeError, OSError):
        return ConvergenceState()


def save_convergence(state: ConvergenceState):
    """Save convergence tracking state."""
    state_file = config.CLAUDE_DIR / "state" / "rsi" / "rsi-convergence-state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(asdict(state), indent=2))


def record_ralph_score(score: int) -> tuple[bool, str]:
    """Record a Ralph score and check for convergence stall.

    Returns (is_stalled, message).
    """
    state = load_convergence()
    stalled = state.record_score(score)
    save_convergence(state)

    if stalled:
        return True, (
            f"CONVERGENCE STALL: Last {config.CONVERGENCE_STALL_COUNT} scores "
            f"({state.recent_scores[-config.CONVERGENCE_STALL_COUNT:]}) all in "
            f"70-{config.DEFAULT_MERGE_THRESHOLD - 1} range. "
            f"Stall count: {state.stall_count}. Consider escalating to user."
        )

    return False, f"Score {score} recorded. Avg: {state.avg_score:.1f}. No stall detected."


# Pattern severity -> candidate impact/urgency mapping
_PATTERN_IMPACT = {
    "missing_tests": (4, 4, 2),     # (impact, urgency, effort)
    "type_hints": (2, 2, 1),
    "error_handling": (4, 5, 3),
    "documentation": (2, 1, 1),
    "code_duplication": (3, 3, 2),
    "security": (5, 5, 3),
    "performance": (3, 3, 3),
}


def create_candidates_from_ralph(patterns: list[dict]) -> list[Candidate]:
    """Convert mined Ralph review patterns into improvement candidates.

    Each pattern with count >= 2 becomes a candidate. Impact/urgency/effort
    are derived from the pattern type and severity.
    """
    candidates = []
    for p in patterns:
        pattern = p.get("pattern", "unknown")
        count = p.get("count", 0)
        severity = p.get("severity", "medium")

        if count < 2:
            continue

        impact, urgency, effort = _PATTERN_IMPACT.get(
            pattern, (3, 3, 2)  # Default medium priority
        )

        # High severity bumps impact
        if severity == "high":
            impact = min(impact + 1, 5)
            urgency = min(urgency + 1, 5)

        candidates.append(Candidate(
            id=f"ralph-{pattern}",
            title=f"Address Ralph feedback: {pattern.replace('_', ' ')} ({count}x)",
            source="ralph",
            impact=impact,
            urgency=urgency,
            effort=effort,
        ))

    return candidates


def feed_ralph_result(
    pr_number: int,
    score: int,
    comments: list[str] | None = None,
) -> tuple[bool, str, list[Candidate]]:
    """Feed Ralph review result into convergence tracker and candidate queue.

    Orchestrates the full feedback loop:
    1. Record score in convergence tracker
    2. If comments provided, save to collection file for mining
    3. Mine patterns from collected comments
    4. Create candidates from patterns
    5. Merge new candidates into queue (skip duplicates)

    Returns (is_stalled, message, new_candidates).
    """
    from .mining import mine_ralph_reviews
    from .queue import load_queue, save_queue

    # 1. Record score
    stalled, msg = record_ralph_score(score)

    # 2. Save comments for mining
    new_candidates = []
    if comments:
        try:
            existing = []
            if config.RALPH_COLLECT.exists():
                existing = json.loads(config.RALPH_COLLECT.read_text())
                if not isinstance(existing, list):
                    existing = existing.get("reviews", [])
            # Add new comments
            for comment in comments:
                existing.append({"body": comment, "pr": pr_number})
            config.RALPH_COLLECT.parent.mkdir(parents=True, exist_ok=True)
            config.RALPH_COLLECT.write_text(json.dumps(existing, indent=2))
        except (json.JSONDecodeError, OSError):
            pass  # Non-critical

    # 3. Mine patterns
    patterns = mine_ralph_reviews()

    # 4. Create candidates from patterns
    if patterns:
        new_candidates = create_candidates_from_ralph(patterns)

    # 5. Merge into queue (skip existing IDs)
    if new_candidates:
        queue = load_queue()
        existing_ids = {c.id for c in queue}
        added = []
        for c in new_candidates:
            if c.id not in existing_ids:
                queue.append(c)
                added.append(c.id)
        if added:
            save_queue(queue)
            msg += f" Added {len(added)} candidate(s) to queue."

    # Record metric
    record_metric("ralph_feed", {
        "pr": pr_number,
        "score": score,
        "stalled": stalled,
        "patterns_found": len(patterns),
        "candidates_created": len(new_candidates),
    })

    return stalled, msg, new_candidates


def check_stop_signal() -> bool:
    """Check if user has requested RSI loop stop."""
    return config.STOP_SIGNAL.exists()


def clear_stop_signal():
    """Clear the stop signal file."""
    if config.STOP_SIGNAL.exists():
        config.STOP_SIGNAL.unlink()


def set_stop_signal(reason: str = "user_requested"):
    """Set stop signal to halt RSI loop."""
    config.STOP_SIGNAL.write_text(json.dumps({
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))
