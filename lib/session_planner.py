"""Session Planner -- pre-session planning from history.

Analyzes historical session data, TODO items, and convergence patterns
to generate recommended session plans with prioritized work items,
focus areas, and estimated durations.

Data sources:
  - converge-metrics JSONL history entries
  - TODO lists (JSON format)
  - Session patterns (frequency, recency, gaps)

CLI:
  python3 lib/session_planner.py plan
  python3 lib/session_planner.py plan --focus code --duration 60
  python3 lib/session_planner.py priorities
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PriorityItem:
    """A single ranked work item."""

    description: str
    source: str  # "todo", "backlog", "pattern"
    priority: int  # 1 (highest) to 5 (lowest)
    estimated_effort_min: int = 30


@dataclass
class SessionPlan:
    """Recommended plan for an upcoming session."""

    focus_areas: list[str] = field(default_factory=list)
    recommended_genes: list[str] = field(default_factory=list)
    estimated_duration_min: int = 120
    priority_items: list[PriorityItem] = field(default_factory=list)
    predicted_outcomes: list[str] = field(default_factory=list)
    confidence: float = 0.5


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Focus areas and their associated keywords for matching
FOCUS_KEYWORDS: dict[str, list[str]] = {
    "code": ["implement", "feature", "create", "build", "add", "develop", "write"],
    "bugfix": ["bug", "fix", "broken", "crash", "error", "fail", "regression"],
    "refactor": ["refactor", "restructure", "reorganize", "clean", "simplify", "dry"],
    "docs": ["document", "readme", "docs", "guide", "tutorial", "comment"],
    "test": ["test", "spec", "coverage", "assert", "verify"],
    "infra": ["infra", "deploy", "ci", "pipeline", "docker", "config"],
    "design": ["design", "architect", "plan", "blueprint", "proposal"],
    "review": ["review", "audit", "check", "inspect", "evaluate"],
    "perf": ["performance", "optimize", "speed", "latency", "cache"],
    "security": ["security", "vulnerability", "permission", "auth", "inject"],
}

# Gene recommendations per focus area
FOCUS_GENES: dict[str, list[str]] = {
    "code": ["precision", "completeness", "efficiency"],
    "bugfix": ["root-cause", "trace", "precision"],
    "refactor": ["simplicity", "dry", "solid"],
    "docs": ["clarity", "completeness", "accuracy"],
    "test": ["coverage", "edge-cases", "isolation"],
    "infra": ["reliability", "simplicity", "completeness"],
    "design": ["creativity", "feasibility", "solid"],
    "review": ["thoroughness", "precision", "context"],
    "perf": ["big-o", "profiling", "efficiency"],
    "security": ["thoroughness", "paranoia", "trace"],
}

# Base effort estimates per source type (minutes)
BASE_EFFORT: dict[str, int] = {
    "todo": 30,
    "backlog": 45,
    "pattern": 20,
}

# Priority label map
PRIORITY_LABELS: dict[int, str] = {
    1: "HIGH",
    2: "HIGH",
    3: "MED",
    4: "LOW",
    5: "LOW",
}


# ---------------------------------------------------------------------------
# History analysis helpers
# ---------------------------------------------------------------------------


def _extract_task_types(history: list[dict]) -> list[str]:
    """Extract task types from history entries."""
    return [h["task_type"] for h in history if "task_type" in h]


def _extract_scores(history: list[dict]) -> list[float]:
    """Extract average scores from history entries."""
    scores = []
    for entry in history:
        dim_scores = entry.get("final_dimension_scores")
        if dim_scores and isinstance(dim_scores, dict):
            vals = [v for v in dim_scores.values() if isinstance(v, (int, float))]
            if vals:
                scores.append(sum(vals) / len(vals))
    return scores


def _success_rate(history: list[dict]) -> float:
    """Calculate success rate from history."""
    if not history:
        return 0.0
    satisfied = sum(1 for h in history if h.get("satisfied"))
    return satisfied / len(history)


def _frequency_rank(items: list[str]) -> list[str]:
    """Rank items by frequency, most common first."""
    if not items:
        return []
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for i, item in enumerate(items):
        counts[item] = counts.get(item, 0) + 1
        if item not in first_seen:
            first_seen[item] = i
    return sorted(counts.keys(), key=lambda x: (-counts[x], first_seen[x]))


def _detect_gaps(history: list[dict]) -> list[str]:
    """Detect focus areas that are underrepresented in history.

    Returns areas that have not appeared or have low success rates,
    suggesting they need attention.
    """
    if not history:
        return list(FOCUS_KEYWORDS.keys())[:3]

    type_counts: dict[str, int] = {}
    type_success: dict[str, int] = {}
    for h in history:
        tt = h.get("task_type", "")
        if tt:
            type_counts[tt] = type_counts.get(tt, 0) + 1
            if h.get("satisfied"):
                type_success[tt] = type_success.get(tt, 0) + 1

    gaps = []
    # Areas never attempted
    for area in FOCUS_KEYWORDS:
        if area not in type_counts:
            gaps.append(area)

    # Areas with low success rate (< 50%)
    for area, count in type_counts.items():
        if count >= 2:
            success = type_success.get(area, 0)
            if success / count < 0.5:
                gaps.append(area)

    return gaps


def _score_variance(scores: list[float]) -> float:
    """Calculate sample variance of scores."""
    if len(scores) < 2:
        return 0.0
    mean = sum(scores) / len(scores)
    return sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)


# ---------------------------------------------------------------------------
# TODO analysis helpers
# ---------------------------------------------------------------------------


def _parse_todo(todo: dict) -> Optional[PriorityItem]:
    """Convert a TODO dict to a PriorityItem.

    Expected format: {"number": 100, "description": "...", "status": "PENDING", "origin": "Gen 331"}
    """
    desc = todo.get("description", "")
    if not desc:
        return None

    status = todo.get("status", "PENDING")
    if status not in ("PENDING", "IN PROGRESS"):
        return None

    number = todo.get("number")
    label = f"TODO #{number}: {desc}" if number else desc

    # Estimate priority from status and age
    priority = 3  # default medium
    if status == "IN PROGRESS":
        priority = 2
    origin = todo.get("origin", "")
    if origin:
        # Older TODOs get slightly higher priority (they've been waiting)
        try:
            gen_num = int(origin.replace("Gen ", "").strip())
            if gen_num < 300:
                priority = max(1, priority - 1)
        except (ValueError, AttributeError):
            pass

    return PriorityItem(
        description=label,
        source="todo",
        priority=priority,
        estimated_effort_min=BASE_EFFORT["todo"],
    )


def _infer_focus_from_description(desc: str) -> Optional[str]:
    """Infer focus area from a text description."""
    desc_lower = desc.lower()
    for area, keywords in FOCUS_KEYWORDS.items():
        for kw in keywords:
            if kw in desc_lower:
                return area
    return None


def _pattern_items_from_history(history: list[dict]) -> list[PriorityItem]:
    """Generate PriorityItems from detected patterns in history.

    Identifies recurring failure patterns, stalled convergences,
    and areas needing improvement.
    """
    items: list[PriorityItem] = []

    if not history:
        return items

    # Check for repeated failures in a type
    type_failures: dict[str, int] = {}
    for h in history:
        if not h.get("satisfied") and h.get("task_type"):
            tt = h["task_type"]
            type_failures[tt] = type_failures.get(tt, 0) + 1

    for tt, count in type_failures.items():
        if count >= 2:
            items.append(PriorityItem(
                description=f"Address {count} stalled convergence patterns in {tt}",
                source="pattern",
                priority=3,
                estimated_effort_min=20,
            ))

    # Check for overconfidence (high predicted scores, low actual)
    scores = _extract_scores(history)
    if len(scores) >= 3:
        variance = _score_variance(scores)
        if variance > 200:
            items.append(PriorityItem(
                description="Improve forecast accuracy -- calibration shows high variance",
                source="pattern",
                priority=3,
                estimated_effort_min=20,
            ))

    # Check for underused focus areas
    gaps = _detect_gaps(history)
    if gaps:
        gap_str = ", ".join(gaps[:3])
        items.append(PriorityItem(
            description=f"Explore underrepresented areas: {gap_str}",
            source="pattern",
            priority=4,
            estimated_effort_min=15,
        ))

    return items


# ---------------------------------------------------------------------------
# Core planning functions
# ---------------------------------------------------------------------------


def rank_priorities(
    todos: Optional[list[dict]] = None,
    history: Optional[list[dict]] = None,
) -> list[PriorityItem]:
    """Rank work items by impact and urgency.

    Combines TODO items with pattern-detected items from history,
    then sorts by priority (ascending = highest first).
    """
    items: list[PriorityItem] = []

    # Parse TODOs
    if todos:
        for todo in todos:
            item = _parse_todo(todo)
            if item:
                items.append(item)

    # Add pattern-detected items from history
    if history:
        items.extend(_pattern_items_from_history(history))

    # Sort: priority ascending (1=highest), then effort ascending
    items.sort(key=lambda x: (x.priority, x.estimated_effort_min))

    return items


def suggest_focus(
    history: Optional[list[dict]] = None,
) -> list[str]:
    """Suggest focus areas based on gaps in history.

    Returns areas that need attention -- either never attempted
    or with low success rates.
    """
    if not history:
        return ["code", "test", "docs"]

    gaps = _detect_gaps(history)
    if gaps:
        return gaps[:3]

    # No clear gaps: suggest most successful areas for momentum
    type_counts = _frequency_rank(_extract_task_types(history))
    return type_counts[:3] if type_counts else ["code"]


def estimate_session_duration(items: list[PriorityItem]) -> int:
    """Estimate total session duration from prioritized items.

    Adds a 15% overhead for context switching between items,
    with a minimum of 30 minutes.
    """
    if not items:
        return 30

    total = sum(item.estimated_effort_min for item in items)

    # Add 15% overhead for context switching (minimum 5 min)
    overhead = max(5, int(total * 0.15))

    return max(30, total + overhead)


def generate_plan(
    history: Optional[list[dict]] = None,
    todos: Optional[list[dict]] = None,
    duration_min: int = 120,
    focus: Optional[str] = None,
) -> SessionPlan:
    """Produce a recommended session plan.

    Analyzes history and TODOs to create a plan that fits within
    the given duration, prioritizing high-impact items.

    Args:
        history: List of converge-metrics JSONL entries.
        todos: List of TODO dicts.
        duration_min: Target session duration in minutes.
        focus: Optional focus area to filter/prioritize.

    Returns:
        SessionPlan with prioritized items and recommendations.
    """
    if history is None:
        history = []
    if todos is None:
        todos = []

    # Rank all items
    all_items = rank_priorities(todos, history)

    # If focus specified, boost matching items
    if focus:
        for item in all_items:
            inferred = _infer_focus_from_description(item.description)
            if inferred == focus:
                item.priority = max(1, item.priority - 1)
        # Re-sort after priority adjustment
        all_items.sort(key=lambda x: (x.priority, x.estimated_effort_min))

    # Select items that fit within duration
    selected: list[PriorityItem] = []
    remaining_min = duration_min
    for item in all_items:
        if remaining_min <= 0:
            break
        # Allow slight overflow (up to 10 min) for the last item
        if item.estimated_effort_min <= remaining_min + 10:
            selected.append(item)
            remaining_min -= item.estimated_effort_min

    # Determine focus areas
    if focus:
        focus_areas = [focus]
    else:
        focus_areas = suggest_focus(history)

    # Determine genes
    genes: list[str] = []
    seen_genes: set[str] = set()
    for area in focus_areas:
        for g in FOCUS_GENES.get(area, []):
            if g not in seen_genes:
                genes.append(g)
                seen_genes.add(g)
    if not genes:
        genes = ["precision", "completeness"]

    # Estimate actual duration
    est_duration = estimate_session_duration(selected) if selected else duration_min

    # Predict outcomes
    outcomes = _predict_outcomes(selected, history)

    # Calculate confidence
    confidence = _plan_confidence(history, selected)

    return SessionPlan(
        focus_areas=focus_areas,
        recommended_genes=genes,
        estimated_duration_min=est_duration,
        priority_items=selected,
        predicted_outcomes=outcomes,
        confidence=confidence,
    )


def _predict_outcomes(
    items: list[PriorityItem],
    history: list[dict],
) -> list[str]:
    """Predict session outcomes based on selected items and history."""
    outcomes = []

    if not items:
        return ["Session planning -- no actionable items identified"]

    # Count items by priority
    high = sum(1 for i in items if i.priority <= 2)
    med = sum(1 for i in items if i.priority == 3)
    low = sum(1 for i in items if i.priority >= 4)

    if high > 0:
        outcomes.append(f"Complete {high} high-priority item(s)")
    if med > 0:
        outcomes.append(f"Address {med} medium-priority item(s)")
    if low > 0:
        outcomes.append(f"Clean up {low} low-priority item(s)")

    # History-based predictions
    if history:
        success_rate = _success_rate(history)
        if success_rate >= 0.7:
            outcomes.append("Strong track record suggests high completion likelihood")
        elif success_rate < 0.4:
            outcomes.append("Historical success rate is low -- consider reducing scope")

    return outcomes


def _plan_confidence(
    history: list[dict],
    items: list[PriorityItem],
) -> float:
    """Calculate confidence in the plan.

    Higher confidence when: more history data, lower variance,
    fewer items (less risk of overcommit).
    """
    if not items:
        return 0.3

    # Base from history sample size
    sample_size = len(history) if history else 0
    if sample_size <= 0:
        size_factor = 0.3
    else:
        size_factor = min(0.8, math.log(sample_size + 1) / math.log(31))

    # Item count penalty: more items = less confidence
    item_penalty = 1.0 / (1.0 + len(items) / 10.0)

    # Variance penalty from history
    variance_penalty = 1.0
    if history:
        scores = _extract_scores(history)
        if scores:
            variance = _score_variance(scores)
            variance_penalty = 1.0 / (1.0 + variance / 100.0)

    confidence = size_factor * item_penalty * variance_penalty

    # Floor at 0.1, cap at 0.95
    return max(0.1, min(0.95, round(confidence, 2)))


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_plan(plan: SessionPlan) -> str:
    """Format a SessionPlan as a human-readable report."""
    lines = [
        f"Session Plan (est. {plan.estimated_duration_min} min)",
        f"Confidence: {plan.confidence:.0%}",
    ]

    if plan.recommended_genes:
        lines.append(f"Recommended genes: {', '.join(plan.recommended_genes)}")

    if plan.focus_areas:
        lines.append(f"Focus areas: {', '.join(plan.focus_areas)}")

    lines.append("")

    if plan.priority_items:
        lines.append("Priority Items:")
        for i, item in enumerate(plan.priority_items, 1):
            label = PRIORITY_LABELS.get(item.priority, "MED")
            lines.append(
                f"  {i}. [{label:<4}] {item.description} ({item.estimated_effort_min} min)"
            )
    else:
        lines.append("Priority Items: None identified")

    if plan.predicted_outcomes:
        lines.append("")
        lines.append("Predicted Outcomes:")
        for outcome in plan.predicted_outcomes:
            lines.append(f"  - {outcome}")

    return "\n".join(lines)


def format_priorities(items: list[PriorityItem]) -> str:
    """Format a ranked priority list."""
    if not items:
        return "No priority items identified."

    lines = ["Ranked Priorities:"]
    for i, item in enumerate(items, 1):
        label = PRIORITY_LABELS.get(item.priority, "MED")
        source_tag = f"[{item.source}]"
        lines.append(
            f"  {i}. [{label:<4}] {source_tag:<10} {item.description} "
            f"({item.estimated_effort_min} min)"
        )

    total = sum(item.estimated_effort_min for item in items)
    lines.append(f"\nTotal estimated effort: {total} min")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def _load_jsonl(path: Path) -> list[dict]:
    """Load entries from a JSONL file."""
    entries = []
    if not path.exists():
        return entries
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def _load_json(path: Path) -> list[dict]:
    """Load a JSON array file."""
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="session-planner",
        description="GRIP Session Planner -- pre-session planning from history.",
    )
    parser.add_argument(
        "command",
        choices=["plan", "priorities"],
        help="Command to run",
    )
    parser.add_argument(
        "--focus",
        help="Focus area to prioritize (e.g., code, bugfix, test)",
        default=None,
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=120,
        help="Target session duration in minutes (default: 120)",
    )
    parser.add_argument(
        "--history",
        help="Path to JSONL history file",
        default=None,
    )
    parser.add_argument(
        "--todos",
        help="Path to JSON todos file",
        default=None,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args(argv)

    # Load data
    history = _load_jsonl(Path(args.history)) if args.history else []
    todos = _load_json(Path(args.todos)) if args.todos else []

    if args.command == "plan":
        plan = generate_plan(
            history=history,
            todos=todos,
            duration_min=args.duration,
            focus=args.focus,
        )
        if args.json:
            import dataclasses
            print(json.dumps(dataclasses.asdict(plan), indent=2))
        else:
            print(format_plan(plan))

    elif args.command == "priorities":
        items = rank_priorities(todos=todos, history=history)
        if args.json:
            import dataclasses
            print(json.dumps([dataclasses.asdict(i) for i in items], indent=2))
        else:
            print(format_priorities(items))

    return 0


if __name__ == "__main__":
    sys.exit(main())
