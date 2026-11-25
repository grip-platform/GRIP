#!/usr/bin/env python3
"""
strategy_advisor.py -- Recommend optimal convergence strategy for next depth.

Analyzes historical convergence runs to find strategies that worked for similar
weaknesses and task types. Provides data-driven advice on what strategy to use
at each depth, replacing gut instinct with empirical evidence.

Usage:
    from lib.strategy_advisor import advise_next, build_strategy_catalog

    # Get advice for current convergence state
    advice = advise_next(
        current_scores={"correctness": 65, "design": 70},
        weaknesses=["no error handling", "missing tests"],
        task_type="code",
        history=loaded_history,
    )
    print(format_advice(advice))

    # Build catalog from history
    catalog = build_strategy_catalog(history)
    print(format_catalog(catalog))

CLI:
    python3 lib/strategy_advisor.py advise <run_id>
    python3 lib/strategy_advisor.py history
    python3 lib/strategy_advisor.py strategies
    python3 lib/strategy_advisor.py match <weakness>
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
CONVERGE_METRICS_PATH = CLAUDE_DIR / "learning" / "converge-metrics.jsonl"

# =========================================================================
# Data Structures
# =========================================================================

# Common English stopwords for tokenization
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "because", "but", "and", "or", "if", "while", "about", "up",
    "it", "its", "this", "that", "these", "those", "i", "me", "my",
    "we", "our", "you", "your", "he", "him", "his", "she", "her",
    "they", "them", "their", "what", "which", "who", "whom",
})


@dataclass
class StrategyAdvice:
    """Recommendation for the next convergence depth strategy."""

    strategy_name: str
    reasoning: str
    confidence: float  # 0.0 - 1.0
    expected_improvement: float  # expected score delta
    matching_history: list[str] = field(default_factory=list)  # run_ids

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, self.confidence))


@dataclass
class StrategyRecord:
    """A strategy extracted from historical convergence data."""

    name: str
    description: str
    task_types: list[str] = field(default_factory=list)
    avg_improvement: float = 0.0
    usage_count: int = 0
    success_rate: float = 0.0  # fraction of uses that improved score


@dataclass
class AccuracyRecord:
    """Tracks whether advice was followed and whether it helped."""

    run_id: str
    advised_strategy: str
    actual_strategy: str
    score_before: float
    score_after: float
    advice_followed: bool
    improvement: float


# =========================================================================
# Tokenization and Similarity
# =========================================================================


def tokenize(text: str) -> set[str]:
    """Extract meaningful tokens from text, removing stopwords and short words."""
    words = re.findall(r"[a-z][a-z0-9_-]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def token_similarity(text_a: str, text_b: str) -> float:
    """Jaccard similarity between token sets of two strings."""
    tokens_a = tokenize(text_a)
    tokens_b = tokenize(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0


# =========================================================================
# History Loading
# =========================================================================


def load_history(path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Load convergence run history from JSONL metrics file.

    Each line is a JSON event. We reconstruct runs from start/depth/end events.
    Falls back to loading as complete run objects (for direct JSON arrays or
    pre-assembled run dicts).
    """
    metrics_path = path or CONVERGE_METRICS_PATH
    if not metrics_path.exists():
        return []

    runs: dict[str, dict] = {}
    raw_runs: list[dict] = []

    try:
        with open(metrics_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # If this is a complete run object (has run_id + depths)
                if "run_id" in event and "depths" in event:
                    raw_runs.append(event)
                    continue

                # Otherwise reconstruct from events
                run_id = event.get("run_id", "")
                event_type = event.get("event", "")

                if event_type == "converge_start":
                    runs[run_id] = {
                        "run_id": run_id,
                        "task_type": event.get("task_type", "general"),
                        "depths": [],
                        "satisfied": False,
                    }
                elif event_type == "converge_depth" and run_id in runs:
                    runs[run_id]["depths"].append({
                        "depth": event.get("depth", 0),
                        "dimension_scores": event.get("dimension_scores", {}),
                        "weaknesses": event.get("weaknesses", []),
                        "strategy": event.get("strategy", ""),
                    })
                elif event_type == "converge_end" and run_id in runs:
                    runs[run_id]["satisfied"] = event.get("satisfied", False)
    except (OSError, PermissionError):
        return []

    result = list(runs.values()) + raw_runs
    return result


# =========================================================================
# Core Functions
# =========================================================================


def build_strategy_catalog(history: list[dict[str, Any]]) -> list[StrategyRecord]:
    """Extract all strategies used across history with their outcomes.

    Groups by strategy name, computes avg improvement, usage count, success rate.
    """
    strategy_data: dict[str, dict] = defaultdict(lambda: {
        "task_types": set(),
        "improvements": [],
        "weaknesses_addressed": [],
    })

    for run in history:
        task_type = run.get("task_type", "general")
        depths = run.get("depths", [])

        for i, depth_info in enumerate(depths):
            strategy = depth_info.get("strategy", "").strip()
            if not strategy:
                continue

            data = strategy_data[strategy]
            data["task_types"].add(task_type)
            data["weaknesses_addressed"].extend(depth_info.get("weaknesses", []))

            # Compute improvement: compare composite score at this depth vs previous
            scores_now = depth_info.get("dimension_scores", {})
            if i > 0:
                scores_prev = depths[i - 1].get("dimension_scores", {})
                improvement = _composite(scores_now) - _composite(scores_prev)
            elif scores_now:
                improvement = _composite(scores_now)
            else:
                improvement = 0.0

            data["improvements"].append(improvement)

    catalog = []
    for name, data in sorted(strategy_data.items()):
        improvements = data["improvements"]
        usage_count = len(improvements)
        avg_imp = sum(improvements) / usage_count if usage_count else 0.0
        success_count = sum(1 for imp in improvements if imp > 0)
        success_rate = success_count / usage_count if usage_count else 0.0

        # Build description from common weaknesses
        weakness_tokens = defaultdict(int)
        for w in data["weaknesses_addressed"]:
            for token in tokenize(w):
                weakness_tokens[token] += 1
        top_tokens = sorted(weakness_tokens, key=weakness_tokens.get, reverse=True)[:5]
        desc = f"Addresses: {', '.join(top_tokens)}" if top_tokens else "General strategy"

        catalog.append(StrategyRecord(
            name=name,
            description=desc,
            task_types=sorted(data["task_types"]),
            avg_improvement=round(avg_imp, 2),
            usage_count=usage_count,
            success_rate=round(success_rate, 3),
        ))

    return catalog


def match_weakness_to_strategy(
    weakness: str,
    catalog: list[StrategyRecord],
    *,
    history: Optional[list[dict[str, Any]]] = None,
) -> list[tuple[StrategyRecord, float]]:
    """Find strategies that historically addressed a similar weakness.

    Returns list of (strategy, relevance_score) sorted by relevance descending.
    Relevance combines token similarity + historical success rate.
    """
    if not catalog:
        return []

    # Build weakness-to-strategy mapping from history if provided
    strategy_weakness_texts: dict[str, str] = {}
    if history:
        for run in history:
            for depth_info in run.get("depths", []):
                strategy = depth_info.get("strategy", "").strip()
                if strategy:
                    weaknesses = depth_info.get("weaknesses", [])
                    existing = strategy_weakness_texts.get(strategy, "")
                    strategy_weakness_texts[strategy] = existing + " " + " ".join(weaknesses)

    results = []
    for record in catalog:
        # Similarity between input weakness and strategy name/description
        name_sim = token_similarity(weakness, record.name + " " + record.description)

        # Similarity to historical weaknesses this strategy addressed
        hist_sim = 0.0
        if record.name in strategy_weakness_texts:
            hist_sim = token_similarity(weakness, strategy_weakness_texts[record.name])

        # Combined relevance: weighted sum of similarities + success rate
        relevance = (0.3 * name_sim + 0.4 * hist_sim + 0.3 * record.success_rate)

        if relevance > 0.01:
            results.append((record, round(relevance, 4)))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def score_strategy(
    strategy: StrategyRecord,
    context: dict[str, Any],
) -> float:
    """Predict effectiveness of a strategy in the current context.

    Context keys:
        task_type: str -- the type of task being converged
        current_scores: dict[str, float] -- dimension scores at current depth
        weaknesses: list[str] -- current weaknesses
        depth: int -- current depth (0-indexed)

    Returns a predicted effectiveness score (0.0 - 1.0).
    """
    score = 0.0

    # Task type match bonus
    task_type = context.get("task_type", "general")
    if task_type in strategy.task_types:
        score += 0.25

    # Success rate contribution
    score += 0.35 * strategy.success_rate

    # Usage confidence: more usage = more reliable estimate
    usage_factor = min(strategy.usage_count / 10.0, 1.0)
    score += 0.15 * usage_factor

    # Improvement magnitude factor
    max_improvement = 50.0  # normalize against a reasonable max
    improvement_factor = min(max(strategy.avg_improvement, 0.0) / max_improvement, 1.0)
    score += 0.25 * improvement_factor

    return round(min(score, 1.0), 4)


def advise_next(
    current_scores: dict[str, float],
    weaknesses: list[str],
    task_type: str = "general",
    history: Optional[list[dict[str, Any]]] = None,
) -> StrategyAdvice:
    """Recommend the next convergence strategy based on current state and history.

    Analyzes weaknesses, matches against historical strategies that resolved
    similar weaknesses, and recommends the highest-scoring option.
    """
    if history is None:
        history = []

    catalog = build_strategy_catalog(history)

    if not catalog:
        return StrategyAdvice(
            strategy_name="explore",
            reasoning="No historical data available. Start with broad exploration.",
            confidence=0.1,
            expected_improvement=0.0,
            matching_history=[],
        )

    context = {
        "task_type": task_type,
        "current_scores": current_scores,
        "weaknesses": weaknesses,
    }

    # Score each strategy in context
    scored: list[tuple[StrategyRecord, float]] = []
    for record in catalog:
        effectiveness = score_strategy(record, context)

        # Boost for weakness match
        weakness_boost = 0.0
        for w in weaknesses:
            matches = match_weakness_to_strategy(w, [record], history=history)
            if matches:
                weakness_boost = max(weakness_boost, matches[0][1])

        combined = 0.6 * effectiveness + 0.4 * weakness_boost
        scored.append((record, combined))

    scored.sort(key=lambda x: x[1], reverse=True)
    best, best_score = scored[0]

    # Find matching run_ids
    matching_ids = _find_matching_run_ids(best.name, history)

    # Build reasoning
    weakness_text = ", ".join(weaknesses) if weaknesses else "none identified"
    reasoning = (
        f"Strategy '{best.name}' selected based on {best.usage_count} historical uses "
        f"with {best.success_rate:.0%} success rate and avg improvement of "
        f"{best.avg_improvement:.1f} points. Current weaknesses: {weakness_text}. "
        f"Task type '{task_type}' "
        f"{'matches' if task_type in best.task_types else 'is new for'} this strategy."
    )

    return StrategyAdvice(
        strategy_name=best.name,
        reasoning=reasoning,
        confidence=round(best_score, 4),
        expected_improvement=best.avg_improvement,
        matching_history=matching_ids[:10],
    )


# =========================================================================
# Accuracy Tracking
# =========================================================================


def track_accuracy(
    advised: StrategyAdvice,
    actual_outcome: dict[str, Any],
) -> AccuracyRecord:
    """Check whether the advice was followed and whether it helped.

    actual_outcome keys:
        run_id: str
        strategy_used: str -- what strategy was actually used
        score_before: float
        score_after: float
    """
    strategy_used = actual_outcome.get("strategy_used", "")
    score_before = actual_outcome.get("score_before", 0.0)
    score_after = actual_outcome.get("score_after", 0.0)
    improvement = score_after - score_before
    followed = strategy_used.lower().strip() == advised.strategy_name.lower().strip()

    return AccuracyRecord(
        run_id=actual_outcome.get("run_id", "unknown"),
        advised_strategy=advised.strategy_name,
        actual_strategy=strategy_used,
        score_before=score_before,
        score_after=score_after,
        advice_followed=followed,
        improvement=improvement,
    )


# =========================================================================
# Formatting
# =========================================================================


def format_advice(advice: StrategyAdvice) -> str:
    """Human-readable advice with reasoning."""
    lines = [
        f"Recommended Strategy: {advice.strategy_name}",
        f"Confidence: {advice.confidence:.0%}",
        f"Expected Improvement: {advice.expected_improvement:+.1f} points",
        "",
        f"Reasoning: {advice.reasoning}",
    ]
    if advice.matching_history:
        lines.append("")
        lines.append(f"Based on {len(advice.matching_history)} similar runs: "
                      f"{', '.join(advice.matching_history[:5])}")
    return "\n".join(lines)


def format_catalog(catalog: list[StrategyRecord]) -> str:
    """Formatted strategy catalog table."""
    if not catalog:
        return "No strategies recorded yet."

    lines = [
        f"{'Strategy':<30} {'Uses':>5} {'Success':>8} {'Avg Imp':>8} {'Types'}",
        "-" * 80,
    ]
    for record in sorted(catalog, key=lambda r: r.success_rate, reverse=True):
        types = ", ".join(record.task_types[:3])
        lines.append(
            f"{record.name:<30} {record.usage_count:>5} "
            f"{record.success_rate:>7.0%} {record.avg_improvement:>+7.1f} "
            f"{types}"
        )
    return "\n".join(lines)


# =========================================================================
# Internal Helpers
# =========================================================================


def _composite(scores: dict[str, float]) -> float:
    """Simple average of dimension scores."""
    if not scores:
        return 0.0
    values = [v for v in scores.values() if isinstance(v, (int, float))]
    return sum(values) / len(values) if values else 0.0


def _find_matching_run_ids(strategy_name: str, history: list[dict]) -> list[str]:
    """Find run_ids where a given strategy was used."""
    ids = []
    for run in history:
        for depth_info in run.get("depths", []):
            if depth_info.get("strategy", "").strip() == strategy_name:
                run_id = run.get("run_id", "")
                if run_id and run_id not in ids:
                    ids.append(run_id)
                break
    return ids


def _get_latest_depth(run: dict) -> Optional[dict]:
    """Get the most recent depth entry from a run."""
    depths = run.get("depths", [])
    return depths[-1] if depths else None


def _find_run_by_id(run_id: str, history: list[dict]) -> Optional[dict]:
    """Find a run by its ID (exact or prefix match)."""
    for run in history:
        rid = run.get("run_id", "")
        if rid == run_id or rid.startswith(run_id):
            return run
    return None


# =========================================================================
# CLI
# =========================================================================


def cli_advise(run_id: str, history: list[dict]) -> str:
    """Suggest strategy for the latest depth of a run."""
    run = _find_run_by_id(run_id, history)
    if not run:
        return f"Run '{run_id}' not found in history."

    latest = _get_latest_depth(run)
    if not latest:
        return f"Run '{run_id}' has no depth entries."

    scores = latest.get("dimension_scores", {})
    weaknesses = latest.get("weaknesses", [])
    task_type = run.get("task_type", "general")

    advice = advise_next(scores, weaknesses, task_type, history)
    return format_advice(advice)


def cli_history(history: list[dict]) -> str:
    """Show past accuracy records summary."""
    if not history:
        return "No convergence history found."

    lines = [f"Total runs: {len(history)}"]
    satisfied = sum(1 for r in history if r.get("satisfied"))
    lines.append(f"Satisfied: {satisfied}/{len(history)} ({satisfied/len(history):.0%})")

    # Strategy frequency
    strategy_counts: dict[str, int] = defaultdict(int)
    for run in history:
        for d in run.get("depths", []):
            s = d.get("strategy", "").strip()
            if s:
                strategy_counts[s] += 1

    if strategy_counts:
        lines.append("\nMost used strategies:")
        for name, count in sorted(strategy_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
            lines.append(f"  {name}: {count}x")

    return "\n".join(lines)


def cli_strategies(history: list[dict]) -> str:
    """Show full strategy catalog."""
    catalog = build_strategy_catalog(history)
    return format_catalog(catalog)


def cli_match(weakness: str, history: list[dict]) -> str:
    """Find strategies for a weakness."""
    catalog = build_strategy_catalog(history)
    matches = match_weakness_to_strategy(weakness, catalog, history=history)

    if not matches:
        return f"No strategies found matching weakness: '{weakness}'"

    lines = [f"Strategies for weakness: '{weakness}'", ""]
    for record, relevance in matches[:10]:
        lines.append(
            f"  {record.name} (relevance: {relevance:.2f}, "
            f"success: {record.success_rate:.0%}, "
            f"avg improvement: {record.avg_improvement:+.1f})"
        )
    return "\n".join(lines)


def main() -> None:
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: python3 lib/strategy_advisor.py {advise|history|strategies|match} [args]")
        sys.exit(1)

    command = sys.argv[1]
    history = load_history()

    if command == "advise":
        if len(sys.argv) < 3:
            print("Usage: python3 lib/strategy_advisor.py advise <run_id>")
            sys.exit(1)
        print(cli_advise(sys.argv[2], history))

    elif command == "history":
        print(cli_history(history))

    elif command == "strategies":
        print(cli_strategies(history))

    elif command == "match":
        if len(sys.argv) < 3:
            print("Usage: python3 lib/strategy_advisor.py match <weakness>")
            sys.exit(1)
        weakness_text = " ".join(sys.argv[2:])
        print(cli_match(weakness_text, history))

    else:
        print(f"Unknown command: {command}")
        print("Commands: advise, history, strategies, match")
        sys.exit(1)


if __name__ == "__main__":
    main()
