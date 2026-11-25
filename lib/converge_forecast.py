"""Convergence Forecast -- predict run outcomes before they start.

Analyzes historical convergence data to forecast scores, depth requirements,
gene recommendations, and optimal strategies for upcoming convergence runs.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class Forecast:
    """Predicted outcome for a convergence run."""

    task_type: str
    predicted_score: float  # 0-100
    predicted_depth: int
    confidence: float  # 0-1
    recommended_genes: list[str] = field(default_factory=list)
    recommended_strategy: str = ""
    matching_recipes: list[str] = field(default_factory=list)
    matching_patterns: list[str] = field(default_factory=list)
    reasoning: str = ""


# -- Known task types and their baseline characteristics --

KNOWN_TASK_TYPES = {
    "code": {"base_score": 72, "base_depth": 2, "typical_genes": ["precision", "completeness", "efficiency"]},
    "bugfix": {"base_score": 68, "base_depth": 2, "typical_genes": ["precision", "root-cause", "trace"]},
    "refactor": {"base_score": 75, "base_depth": 1, "typical_genes": ["simplicity", "dry", "solid"]},
    "docs": {"base_score": 80, "base_depth": 1, "typical_genes": ["clarity", "completeness", "accuracy"]},
    "test": {"base_score": 78, "base_depth": 1, "typical_genes": ["coverage", "edge-cases", "isolation"]},
    "review": {"base_score": 70, "base_depth": 2, "typical_genes": ["thoroughness", "precision", "context"]},
    "design": {"base_score": 65, "base_depth": 3, "typical_genes": ["creativity", "feasibility", "solid"]},
    "security": {"base_score": 60, "base_depth": 3, "typical_genes": ["thoroughness", "paranoia", "trace"]},
    "perf": {"base_score": 67, "base_depth": 2, "typical_genes": ["big-o", "profiling", "efficiency"]},
    "infra": {"base_score": 70, "base_depth": 2, "typical_genes": ["reliability", "simplicity", "completeness"]},
}

# Strategy recommendations based on weakness patterns
STRATEGY_MAP = {
    "low_precision": "depth-first",
    "low_coverage": "breadth-first",
    "high_complexity": "decompose",
    "low_confidence": "exploratory",
    "multi_actor": "trace-all-actors",
    "unknown_type": "exploratory",
    "high_variance": "conservative",
    "strong_history": "repeat-best",
}

# Recipes: named convergence patterns
RECIPES = {
    "quick-fix": {"task_types": ["bugfix"], "max_depth": 1, "min_score": 80},
    "deep-dive": {"task_types": ["security", "design"], "max_depth": 4, "min_score": 70},
    "polish": {"task_types": ["docs", "refactor"], "max_depth": 1, "min_score": 85},
    "full-trace": {"task_types": ["bugfix", "security"], "max_depth": 3, "min_score": 75},
    "standard": {"task_types": ["code", "test", "infra", "perf", "review"], "max_depth": 2, "min_score": 75},
}


def _extract_scores(history: list[dict]) -> list[float]:
    """Extract final aggregate scores from history entries."""
    scores = []
    for entry in history:
        dim_scores = entry.get("final_dimension_scores")
        if dim_scores and isinstance(dim_scores, dict):
            vals = [v for v in dim_scores.values() if isinstance(v, (int, float))]
            if vals:
                scores.append(sum(vals) / len(vals))
    return scores


def _filter_history(history: list[dict], task_type: Optional[str] = None) -> list[dict]:
    """Filter history entries by task type."""
    if not task_type:
        return list(history)
    return [h for h in history if h.get("task_type") == task_type]


def _extract_depths(history: list[dict]) -> list[int]:
    """Extract depth_reached values from history."""
    return [h["depth_reached"] for h in history if "depth_reached" in h and isinstance(h["depth_reached"], int)]


def _extract_genes(history: list[dict]) -> list[str]:
    """Extract all genes invoked across history, preserving frequency."""
    genes = []
    for h in history:
        invoked = h.get("invoked_genes")
        if invoked and isinstance(invoked, list):
            genes.extend(invoked)
    return genes


def _extract_strategies(history: list[dict]) -> list[str]:
    """Extract strategies used across history depths."""
    strategies = []
    for h in history:
        depths = h.get("depths")
        if depths and isinstance(depths, list):
            for d in depths:
                s = d.get("strategy") if isinstance(d, dict) else None
                if s:
                    strategies.append(s)
    return strategies


def _success_rate(history: list[dict]) -> float:
    """Calculate success rate from history."""
    if not history:
        return 0.0
    satisfied = sum(1 for h in history if h.get("satisfied"))
    return satisfied / len(history)


def _score_variance(scores: list[float]) -> float:
    """Calculate variance of scores."""
    if len(scores) < 2:
        return 0.0
    mean = sum(scores) / len(scores)
    return sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)


def _frequency_rank(items: list[str]) -> list[str]:
    """Rank items by frequency, most common first, stable order for ties."""
    if not items:
        return []
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for i, item in enumerate(items):
        counts[item] = counts.get(item, 0) + 1
        if item not in first_seen:
            first_seen[item] = i
    ranked = sorted(counts.keys(), key=lambda x: (-counts[x], first_seen[x]))
    return ranked


def assess_confidence(sample_size: int, score_variance: float) -> float:
    """Assess prediction confidence from sample size and score variance.

    Returns a float between 0 and 1. More samples and lower variance
    increase confidence.
    """
    if sample_size <= 0:
        return 0.1  # minimal confidence with no data

    # Sample size factor: logarithmic growth, caps around 30 samples
    size_factor = min(1.0, math.log(sample_size + 1) / math.log(31))

    # Variance penalty: high variance reduces confidence
    # Normalize: variance of 100 (std=10) drops confidence by ~50%
    variance_penalty = 1.0 / (1.0 + score_variance / 100.0)

    confidence = size_factor * variance_penalty

    # Floor at 0.1, cap at 0.95 (never fully certain)
    return max(0.1, min(0.95, confidence))


def estimate_score(
    task_type: Optional[str] = None,
    genes: Optional[list[str]] = None,
    history: Optional[list[dict]] = None,
) -> float:
    """Predict final score from historical averages and task type baseline.

    Blends baseline score for the task type with historical average,
    weighted by sample size.
    """
    baseline = KNOWN_TASK_TYPES.get(task_type or "", {}).get("base_score", 70)

    if not history:
        return float(baseline)

    filtered = _filter_history(history, task_type)
    scores = _extract_scores(filtered)

    if not scores:
        # Fall back to all history if no type-specific data
        scores = _extract_scores(history)
        if not scores:
            return float(baseline)

    hist_avg = sum(scores) / len(scores)

    # Blend: more history = trust history more
    weight = min(0.8, len(scores) / 20.0)
    blended = baseline * (1 - weight) + hist_avg * weight

    # Gene bonus: if requested genes match historically successful genes
    if genes and history:
        successful = [h for h in history if h.get("satisfied")]
        successful_genes = _extract_genes(successful)
        if successful_genes:
            gene_set = set(successful_genes)
            overlap = sum(1 for g in genes if g in gene_set)
            if genes:
                gene_bonus = (overlap / len(genes)) * 5  # up to +5 points
                blended += gene_bonus

    return max(0.0, min(100.0, round(blended, 1)))


def estimate_depth(
    task_type: Optional[str] = None,
    history: Optional[list[dict]] = None,
) -> int:
    """Predict depth needed based on task type and historical patterns."""
    baseline = KNOWN_TASK_TYPES.get(task_type or "", {}).get("base_depth", 2)

    if not history:
        return baseline

    filtered = _filter_history(history, task_type)
    depths = _extract_depths(filtered)

    if not depths:
        depths = _extract_depths(history)
        if not depths:
            return baseline

    hist_avg = sum(depths) / len(depths)

    # Blend with baseline
    weight = min(0.7, len(depths) / 15.0)
    blended = baseline * (1 - weight) + hist_avg * weight

    # Round up: better to over-prepare than under-prepare
    return max(1, math.ceil(blended))


def suggest_genes(
    task_type: Optional[str] = None,
    history: Optional[list[dict]] = None,
) -> list[str]:
    """Recommend gene combination based on task type and historical success.

    Prioritizes genes that appeared in successful convergence runs.
    Falls back to typical genes for the task type.
    """
    typical = list(KNOWN_TASK_TYPES.get(task_type or "", {}).get("typical_genes", []))

    if not history:
        return typical if typical else ["precision", "completeness"]

    # Genes from successful runs (type-specific first, then all)
    filtered = _filter_history(history, task_type)
    successful = [h for h in filtered if h.get("satisfied")]
    if not successful:
        successful = [h for h in history if h.get("satisfied")]

    if not successful:
        return typical if typical else ["precision", "completeness"]

    successful_genes = _extract_genes(successful)
    ranked = _frequency_rank(successful_genes)

    # Take top genes, blended with typical
    result = []
    for g in ranked[:5]:
        if g not in result:
            result.append(g)
    for g in typical:
        if g not in result:
            result.append(g)

    return result[:6]  # cap at 6


def suggest_strategy(
    task_type: Optional[str] = None,
    weaknesses_likely: Optional[list[str]] = None,
    history: Optional[list[dict]] = None,
) -> str:
    """Recommend starting strategy based on context.

    Considers task type, likely weaknesses, and historical strategy success.
    """
    # Check history for best strategies
    if history:
        filtered = _filter_history(history, task_type)
        successful = [h for h in filtered if h.get("satisfied")]
        if successful:
            strategies = _extract_strategies(successful)
            if strategies:
                ranked = _frequency_rank(strategies)
                if ranked:
                    return ranked[0]

    # Weakness-based recommendation
    if weaknesses_likely:
        for weakness in weaknesses_likely:
            key = weakness.lower().replace(" ", "_")
            if key in STRATEGY_MAP:
                return STRATEGY_MAP[key]

    # Task-type defaults
    if task_type in ("security", "design"):
        return "depth-first"
    if task_type in ("docs", "refactor"):
        return "breadth-first"
    if task_type == "bugfix":
        return "trace-all-actors"

    return "balanced"


def _match_recipes(task_type: Optional[str]) -> list[str]:
    """Find matching recipes for a task type."""
    if not task_type:
        return ["standard"]
    matches = []
    for name, recipe in RECIPES.items():
        if task_type in recipe.get("task_types", []):
            matches.append(name)
    return matches if matches else ["standard"]


def _match_patterns(history: list[dict], task_type: Optional[str] = None) -> list[str]:
    """Identify patterns in historical data."""
    patterns = []
    filtered = _filter_history(history, task_type)

    if not filtered:
        return ["no-history"]

    success_rate = _success_rate(filtered)
    if success_rate >= 0.8:
        patterns.append("high-success-rate")
    elif success_rate <= 0.4:
        patterns.append("low-success-rate")

    scores = _extract_scores(filtered)
    if scores:
        variance = _score_variance(scores)
        if variance > 200:
            patterns.append("high-variance")
        elif variance < 25:
            patterns.append("stable-scores")

    depths = _extract_depths(filtered)
    if depths:
        avg_depth = sum(depths) / len(depths)
        if avg_depth > 2.5:
            patterns.append("deep-runs")
        elif avg_depth <= 1.2:
            patterns.append("shallow-runs")

    if not patterns:
        patterns.append("typical")

    return patterns


def _infer_task_type(description: str) -> Optional[str]:
    """Infer task type from description keywords."""
    desc_lower = description.lower()

    # Order matters: more specific types checked first to avoid
    # generic keywords (e.g., "check") matching before domain keywords
    type_keywords = [
        ("bugfix", ["bug", "fix", "broken", "crash", "error", "fail", "regression"]),
        ("security", ["security", "vulnerability", "permission", "access control", "inject", "authentication"]),
        ("refactor", ["refactor", "restructure", "reorganize", "clean up", "simplify"]),
        ("docs", ["document", "readme", "docs", "guide", "tutorial", "comment"]),
        ("test", ["test", "spec", "coverage", "assert", "verify"]),
        ("perf", ["performance", "optimize", "speed", "latency", "throughput", "cache", "profile"]),
        ("infra", ["infra", "deploy", "ci", "pipeline", "docker", "config"]),
        ("design", ["design", "architect", "plan", "blueprint", "proposal"]),
        ("review", ["review", "audit", "check", "inspect", "evaluate"]),
        ("code", ["implement", "feature", "create", "build", "add", "develop"]),
    ]

    for task_type, keywords in type_keywords:
        for kw in keywords:
            if kw in desc_lower:
                return task_type

    return None


def forecast_run(
    task_description: str,
    task_type: Optional[str] = None,
    genes: Optional[list[str]] = None,
    history: Optional[list[dict]] = None,
) -> Forecast:
    """Produce a forecast for a convergence run.

    This is the main entry point. It combines all estimation functions
    to produce a complete Forecast.
    """
    if history is None:
        history = []

    # Infer task type if not provided
    effective_type = task_type or _infer_task_type(task_description) or "code"

    # Estimate components
    predicted_score = estimate_score(effective_type, genes, history)
    predicted_depth = estimate_depth(effective_type, history)
    recommended_genes = genes if genes else suggest_genes(effective_type, history)

    # Identify likely weaknesses from patterns
    patterns = _match_patterns(history, effective_type)
    weaknesses = []
    if "low-success-rate" in patterns:
        weaknesses.append("low_confidence")
    if "high-variance" in patterns:
        weaknesses.append("high_variance")

    recommended_strategy = suggest_strategy(effective_type, weaknesses, history)
    matching_recipes = _match_recipes(effective_type)

    # Confidence assessment
    filtered = _filter_history(history, effective_type)
    scores = _extract_scores(filtered)
    sample_size = len(filtered) if filtered else len(history)
    variance = _score_variance(scores) if scores else 500.0
    confidence = assess_confidence(sample_size, variance)

    # Build reasoning
    reasoning_parts = []
    reasoning_parts.append(f"Task type: {effective_type}")
    if task_type is None:
        reasoning_parts.append(f"(inferred from description)")
    reasoning_parts.append(f"Historical sample: {sample_size} runs")
    if scores:
        reasoning_parts.append(f"Historical avg score: {sum(scores)/len(scores):.1f}")
    reasoning_parts.append(f"Patterns: {', '.join(patterns)}")
    reasoning_parts.append(f"Confidence: {confidence:.2f}")

    return Forecast(
        task_type=effective_type,
        predicted_score=predicted_score,
        predicted_depth=predicted_depth,
        confidence=confidence,
        recommended_genes=recommended_genes,
        recommended_strategy=recommended_strategy,
        matching_recipes=matching_recipes,
        matching_patterns=patterns,
        reasoning=" | ".join(reasoning_parts),
    )


def format_forecast(forecast: Forecast) -> str:
    """Format a Forecast as a human-readable report."""
    lines = [
        "--- Convergence Forecast ---",
        "",
        f"Task Type:            {forecast.task_type}",
        f"Predicted Score:      {forecast.predicted_score}/100",
        f"Predicted Depth:      {forecast.predicted_depth}",
        f"Confidence:           {forecast.confidence:.0%}",
        "",
        f"Recommended Strategy: {forecast.recommended_strategy}",
        f"Recommended Genes:    {', '.join(forecast.recommended_genes) if forecast.recommended_genes else 'none'}",
        "",
        f"Matching Recipes:     {', '.join(forecast.matching_recipes) if forecast.matching_recipes else 'none'}",
        f"Matching Patterns:    {', '.join(forecast.matching_patterns) if forecast.matching_patterns else 'none'}",
        "",
        f"Reasoning: {forecast.reasoning}",
        "",
        "---",
    ]
    return "\n".join(lines)


def _load_history(path: Path) -> list[dict]:
    """Load history from a JSONL file."""
    entries = []
    if not path.exists():
        return entries
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="converge-forecast",
        description="Predict convergence run outcomes before they start.",
    )
    parser.add_argument("task_description", help="Description of the task to forecast")
    parser.add_argument("--task-type", help="Override inferred task type")
    parser.add_argument("--genes", help="Comma-separated gene list")
    parser.add_argument(
        "--history",
        help="Path to JSONL history file",
        default=None,
    )

    args = parser.parse_args(argv)

    genes = [g.strip() for g in args.genes.split(",")] if args.genes else None

    history = []
    if args.history:
        history = _load_history(Path(args.history))

    forecast = forecast_run(
        task_description=args.task_description,
        task_type=args.task_type,
        genes=genes,
        history=history,
    )

    print(format_forecast(forecast))
    return 0


# ---------------------------------------------------------------------------
# Predictor (merged from converge_predictor.py)
# ---------------------------------------------------------------------------

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
METRICS_PATH = CLAUDE_DIR / "learning" / "converge-metrics.jsonl"
STRATEGY_STORE = CLAUDE_DIR / "cache" / "convergence-strategies.json"

# Feature weights for the prediction model
WEIGHTS = {
    "base_rate": 0.35,
    "depth_factor": 0.15,
    "gene_factor": 0.10,
    "hour_factor": 0.10,
    "task_similarity": 0.15,
    "velocity_factor": 0.15,
}

# Known task types and their default base rates (used when no history exists)
DEFAULT_BASE_RATES = {
    "code": 0.85,
    "analysis": 0.80,
    "design": 0.75,
    "creative": 0.70,
    "general": 0.75,
    "explanation": 0.80,
}

DEFAULT_STRATEGY = "iterative-deepening"


@dataclass
class PredictionResult:
    """Result of a convergence success prediction."""

    probability: float
    recommended_depth: int
    best_strategy: str
    confidence: float
    reasoning: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunRecord:
    """Parsed record of a single converge run from metrics history."""

    run_id: str
    task: str
    task_type: str
    max_depth: int
    satisfied: bool
    final_score: float
    depth_reached: int
    elapsed_minutes: float
    velocity_trajectory: list[float]
    expressed_genes: list[str]
    timestamp: str
    strategy: str
    commits: int

    @property
    def hour(self) -> int:
        """Extract hour of day from timestamp."""
        try:
            dt = datetime.fromisoformat(self.timestamp)
            return dt.hour
        except (ValueError, TypeError):
            return 12  # default midday


def load_history(metrics_path: Optional[Path] = None) -> list[dict]:
    """Load all past converge events from JSONL file.

    Returns a list of raw event dicts from the metrics file.
    Each line is one JSON event (converge_start, converge_depth, converge_end).
    """
    p = metrics_path or METRICS_PATH
    if not p.exists():
        return []
    entries = []
    try:
        text = p.read_text()
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _parse_runs(events: list[dict]) -> list[RunRecord]:
    """Parse raw events into structured RunRecord objects.

    Groups events by run_id and extracts start/depth/end data into
    a single RunRecord per completed run.
    """
    runs: dict[str, dict] = {}

    for ev in events:
        rid = ev.get("run_id")
        if not rid:
            continue
        if rid not in runs:
            runs[rid] = {"start": None, "end": None, "depths": []}
        etype = ev.get("event", "")
        if etype == "converge_start":
            runs[rid]["start"] = ev
        elif etype == "converge_end":
            runs[rid]["end"] = ev
        elif etype == "converge_depth":
            runs[rid]["depths"].append(ev)

    records = []
    for rid, data in runs.items():
        start = data.get("start")
        end = data.get("end")
        if not start or not end:
            continue  # incomplete run

        depths = data.get("depths", [])
        strategies = [d.get("strategy", "") for d in depths if d.get("strategy")]
        last_strategy = strategies[-1] if strategies else DEFAULT_STRATEGY

        records.append(RunRecord(
            run_id=rid,
            task=start.get("task", ""),
            task_type=start.get("task_type", "general"),
            max_depth=start.get("max_depth", 3),
            satisfied=end.get("satisfied", False),
            final_score=end.get("final_score", 0.0),
            depth_reached=end.get("depth_reached", 0),
            elapsed_minutes=end.get("elapsed_minutes", 0.0),
            velocity_trajectory=end.get("velocity_trajectory", []),
            expressed_genes=start.get("expressed_genes", []),
            timestamp=end.get("timestamp", start.get("timestamp", "")),
            strategy=last_strategy,
            commits=end.get("commits", 0),
        ))

    return records


def _compute_base_rates(records: list[RunRecord]) -> dict[str, float]:
    """Calculate per-task-type success rate from historical data."""
    type_counts: dict[str, int] = {}
    type_successes: dict[str, int] = {}

    for r in records:
        tt = r.task_type
        type_counts[tt] = type_counts.get(tt, 0) + 1
        if r.satisfied:
            type_successes[tt] = type_successes.get(tt, 0) + 1

    rates = {}
    for tt, count in type_counts.items():
        rates[tt] = type_successes.get(tt, 0) / count
    return rates


def _compute_avg_depths(records: list[RunRecord]) -> dict[str, float]:
    """Calculate average convergence depth per task type."""
    type_depths: dict[str, list[int]] = {}
    for r in records:
        if r.satisfied:
            type_depths.setdefault(r.task_type, []).append(r.depth_reached)

    return {
        tt: sum(depths) / len(depths) if depths else 2.0
        for tt, depths in type_depths.items()
    }


def _compute_avg_velocity(records: list[RunRecord]) -> dict[str, float]:
    """Calculate average velocity (score gain per depth) per task type."""
    type_velocities: dict[str, list[float]] = {}
    for r in records:
        traj = r.velocity_trajectory
        if len(traj) >= 2:
            velocity = (traj[-1] - traj[0]) / max(len(traj) - 1, 1)
            type_velocities.setdefault(r.task_type, []).append(velocity)

    return {
        tt: sum(vels) / len(vels) if vels else 20.0
        for tt, vels in type_velocities.items()
    }


def _compute_hour_success(records: list[RunRecord]) -> dict[int, float]:
    """Calculate success rate by hour of day (bucketed into 4-hour windows)."""
    bucket_total: dict[int, int] = {}
    bucket_success: dict[int, int] = {}

    for r in records:
        bucket = (r.hour // 4) * 4  # 0, 4, 8, 12, 16, 20
        bucket_total[bucket] = bucket_total.get(bucket, 0) + 1
        if r.satisfied:
            bucket_success[bucket] = bucket_success.get(bucket, 0) + 1

    rates = {}
    for bucket, total in bucket_total.items():
        rates[bucket] = bucket_success.get(bucket, 0) / total
    return rates


def _task_similarity_score(task: str, records: list[RunRecord], task_type: str) -> float:
    """Score task similarity to historical runs using keyword overlap.

    Simple word-overlap metric -- counts shared words between the
    prediction task and historical tasks of the same type.
    Returns a normalized score 0-1.
    """
    task_words = set(re.findall(r'\w+', task.lower()))
    if not task_words:
        return 0.5  # neutral

    same_type = [r for r in records if r.task_type == task_type]
    if not same_type:
        return 0.5

    # Find best matching historical task
    best_overlap = 0.0
    best_success = False
    for r in same_type:
        hist_words = set(re.findall(r'\w+', r.task.lower()))
        if not hist_words:
            continue
        overlap = len(task_words & hist_words) / max(len(task_words | hist_words), 1)
        if overlap > best_overlap:
            best_overlap = overlap
            best_success = r.satisfied

    if best_overlap < 0.1:
        return 0.5  # no meaningful similarity

    # Similar tasks that succeeded boost probability; failed ones lower it
    return 0.7 + (0.3 * best_overlap) if best_success else 0.3 + (0.2 * best_overlap)


def _get_strategy_recommendation(
    task_type: str,
    task: str,
    records: list[RunRecord],
) -> str:
    """Get strategy recommendation from historical data.

    Attempts to use converge_strategy_explorer if available,
    falls back to most successful strategy from history.
    """
    try:
        # Try to import the strategy explorer
        if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
            sys.path.insert(0, str(Path(__file__).resolve().parent))
        from converge_strategy_explorer import (
            build_profiles,
            load_strategies,
            recommend_strategy,
        )
        events = load_strategies()
        profiles = build_profiles(events)
        rec = recommend_strategy(
            task_type=task_type,
            profiles=profiles,
            task_hint=task,
        )
        if rec:
            return rec.name
    except (ImportError, Exception):
        pass

    # Fallback: find most used successful strategy from history
    strategy_counts: dict[str, int] = {}
    same_type = [r for r in records if r.task_type == task_type and r.satisfied]
    for r in same_type:
        if r.strategy:
            strategy_counts[r.strategy] = strategy_counts.get(r.strategy, 0) + 1

    if strategy_counts:
        return max(strategy_counts, key=strategy_counts.get)  # type: ignore[arg-type]

    return DEFAULT_STRATEGY


def extract_features(
    task: str,
    task_type: str,
    max_depth: int,
    records: Optional[list[RunRecord]] = None,
    events: Optional[list[dict]] = None,
) -> dict:
    """Extract prediction features for a given task.

    Features:
      - base_rate: historical success rate for this task_type
      - depth_factor: normalized depth signal (deeper = more recovery room)
      - gene_factor: average gene expression for this task type
      - hour_factor: success rate at current hour
      - task_similarity: keyword overlap with successful past tasks
      - velocity_factor: historical velocity for this task type
    """
    if records is None:
        raw = events if events is not None else load_history()
        records = _parse_runs(raw)

    base_rates = _compute_base_rates(records)
    avg_depths = _compute_avg_depths(records)
    avg_velocities = _compute_avg_velocity(records)
    hour_rates = _compute_hour_success(records)

    # Base rate
    base_rate = base_rates.get(task_type, DEFAULT_BASE_RATES.get(task_type, 0.75))

    # Depth factor: having more depth available improves chances
    historical_depth = avg_depths.get(task_type, 2.0)
    depth_factor = min(max_depth / max(historical_depth, 1.0), 1.5) / 1.5

    # Gene factor: average gene count for this task type
    same_type_genes = [
        len(r.expressed_genes) for r in records
        if r.task_type == task_type and r.expressed_genes
    ]
    avg_genes = sum(same_type_genes) / len(same_type_genes) if same_type_genes else 0
    gene_factor = min(avg_genes / 10.0, 1.0) if avg_genes > 0 else 0.5

    # Hour factor
    now_hour = datetime.now(timezone.utc).hour
    bucket = (now_hour // 4) * 4
    hour_factor = hour_rates.get(bucket, 0.75)

    # Task similarity
    similarity = _task_similarity_score(task, records, task_type)

    # Velocity factor: higher historical velocity = better outlook
    avg_vel = avg_velocities.get(task_type, 20.0)
    velocity_factor = min(avg_vel / 30.0, 1.0)

    return {
        "base_rate": round(base_rate, 4),
        "depth_factor": round(depth_factor, 4),
        "gene_factor": round(gene_factor, 4),
        "hour_factor": round(hour_factor, 4),
        "task_similarity": round(similarity, 4),
        "velocity_factor": round(velocity_factor, 4),
    }


def predict_success(
    task: str,
    task_type: str,
    max_depth: int = 3,
    metrics_path: Optional[Path] = None,
) -> PredictionResult:
    """Predict convergence run success probability.

    Combines weighted features into a probability score (0-1),
    recommends optimal depth and best strategy.
    """
    events = load_history(metrics_path)
    records = _parse_runs(events)

    features = extract_features(
        task=task,
        task_type=task_type,
        max_depth=max_depth,
        records=records,
    )

    # Weighted combination
    probability = sum(
        WEIGHTS[feat] * features[feat]
        for feat in WEIGHTS
    )
    # Clamp to [0, 1]
    probability = max(0.0, min(1.0, probability))

    # Recommended depth
    avg_depths_map = _compute_avg_depths(records)
    historical_avg = avg_depths_map.get(task_type, 2.0)
    # Recommend ceiling of historical average, capped at max_depth
    recommended_depth = min(math.ceil(historical_avg), max_depth)
    # Ensure at least depth 1
    recommended_depth = max(1, recommended_depth)

    # Strategy recommendation
    best_strategy = _get_strategy_recommendation(task_type, task, records)

    # Confidence: based on sample size for this task type
    same_type_count = sum(1 for r in records if r.task_type == task_type)
    if same_type_count == 0:
        pred_confidence = 0.3  # no data -- low confidence
    elif same_type_count < 3:
        pred_confidence = 0.5  # sparse data
    elif same_type_count < 10:
        pred_confidence = 0.7  # moderate data
    else:
        pred_confidence = 0.9  # good data

    # Build reasoning
    reasoning_parts = []
    reasoning_parts.append(
        f"Base rate for '{task_type}': {features['base_rate']:.0%}"
    )
    reasoning_parts.append(
        f"Historical data: {same_type_count} runs of type '{task_type}'"
    )
    if features["task_similarity"] > 0.6:
        reasoning_parts.append("Similar tasks found in history (positive signal)")
    elif features["task_similarity"] < 0.4:
        reasoning_parts.append("No similar tasks in history (neutral)")
    if max_depth > historical_avg:
        reasoning_parts.append(
            f"Depth {max_depth} exceeds historical avg {historical_avg:.1f} (extra room)"
        )
    reasoning = ". ".join(reasoning_parts) + "."

    return PredictionResult(
        probability=round(probability, 4),
        recommended_depth=recommended_depth,
        best_strategy=best_strategy,
        confidence=round(pred_confidence, 2),
        reasoning=reasoning,
    )


def get_model_stats(metrics_path: Optional[Path] = None) -> dict:
    """Calculate overall prediction model statistics.

    Returns aggregate data about historical convergence runs for
    model introspection and calibration.
    """
    events = load_history(metrics_path)
    records = _parse_runs(events)

    if not records:
        return {
            "total_runs": 0,
            "task_types": {},
            "overall_success_rate": 0.0,
            "avg_depth": 0.0,
            "avg_elapsed_minutes": 0.0,
            "feature_weights": WEIGHTS,
        }

    base_rates = _compute_base_rates(records)
    avg_depths_map = _compute_avg_depths(records)
    avg_velocities = _compute_avg_velocity(records)

    total = len(records)
    successes = sum(1 for r in records if r.satisfied)
    overall_rate = successes / total if total else 0.0

    all_depths = [r.depth_reached for r in records]
    avg_depth_val = sum(all_depths) / len(all_depths) if all_depths else 0.0

    all_elapsed = [r.elapsed_minutes for r in records if r.elapsed_minutes > 0]
    avg_elapsed = sum(all_elapsed) / len(all_elapsed) if all_elapsed else 0.0

    type_stats = {}
    for tt in sorted(set(r.task_type for r in records)):
        type_runs = [r for r in records if r.task_type == tt]
        type_stats[tt] = {
            "runs": len(type_runs),
            "success_rate": round(base_rates.get(tt, 0.0), 4),
            "avg_depth": round(avg_depths_map.get(tt, 0.0), 2),
            "avg_velocity": round(avg_velocities.get(tt, 0.0), 2),
        }

    return {
        "total_runs": total,
        "total_successes": successes,
        "overall_success_rate": round(overall_rate, 4),
        "avg_depth": round(avg_depth_val, 2),
        "avg_elapsed_minutes": round(avg_elapsed, 1),
        "task_types": type_stats,
        "feature_weights": WEIGHTS,
    }


def format_prediction(result: PredictionResult) -> str:
    """Format prediction result as human-readable text."""
    lines = [
        "Convergence Prediction",
        "=" * 40,
        f"  Success probability:  {result.probability:.0%}",
        f"  Recommended depth:    {result.recommended_depth}",
        f"  Best strategy:        {result.best_strategy}",
        f"  Confidence:           {result.confidence:.0%}",
        "",
        "Reasoning:",
        f"  {result.reasoning}",
    ]
    return "\n".join(lines)


def format_stats(stats: dict) -> str:
    """Format model statistics as human-readable text."""
    lines = [
        "Convergence Prediction Model Statistics",
        "=" * 45,
        f"  Total runs:           {stats['total_runs']}",
        f"  Overall success rate: {stats.get('overall_success_rate', 0):.0%}",
        f"  Average depth:        {stats.get('avg_depth', 0):.1f}",
        f"  Average elapsed:      {stats.get('avg_elapsed_minutes', 0):.1f} min",
        "",
        "Per-Type Breakdown:",
    ]

    type_stats = stats.get("task_types", {})
    if type_stats:
        # Header
        lines.append(f"  {'Type':<12} {'Runs':>5} {'Success':>8} {'Depth':>6} {'Vel':>6}")
        lines.append(f"  {'-' * 12} {'-' * 5} {'-' * 8} {'-' * 6} {'-' * 6}")
        for tt, ts in type_stats.items():
            lines.append(
                f"  {tt:<12} {ts['runs']:>5} "
                f"{ts['success_rate']:>7.0%} "
                f"{ts['avg_depth']:>6.1f} "
                f"{ts.get('avg_velocity', 0):>6.1f}"
            )
    else:
        lines.append("  No data available.")

    lines.append("")
    lines.append("Feature Weights:")
    for feat, weight in WEIGHTS.items():
        lines.append(f"  {feat:<18} {weight:.2f}")

    return "\n".join(lines)


def build_cli() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the predictor."""
    parser = argparse.ArgumentParser(
        description="Convergence prediction model",
        prog="converge_predictor",
    )
    subparsers = parser.add_subparsers(dest="command")

    # predict subcommand
    pred = subparsers.add_parser("predict", help="Predict converge run success")
    pred.add_argument("task", help="Task description")
    pred.add_argument("--type", dest="task_type", default="general",
                       help="Task type (code, analysis, design, creative, general)")
    pred.add_argument("--depth", type=int, default=3,
                       help="Max depth for the run (default: 3)")
    pred.add_argument("--json", action="store_true", dest="json_output",
                       help="Output as JSON")
    pred.add_argument("--metrics-path", help="Override metrics JSONL path")

    # stats subcommand
    st = subparsers.add_parser("stats", help="Show model statistics")
    st.add_argument("--json", action="store_true", dest="json_output",
                     help="Output as JSON")
    st.add_argument("--metrics-path", help="Override metrics JSONL path")

    return parser


def _predictor_main() -> int:
    """CLI entry point for predictor commands."""
    parser = build_cli()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    metrics_p = Path(args.metrics_path) if getattr(args, "metrics_path", None) else None

    if args.command == "predict":
        result = predict_success(
            task=args.task,
            task_type=args.task_type,
            max_depth=args.depth,
            metrics_path=metrics_p,
        )
        if args.json_output:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(format_prediction(result))
        return 0

    elif args.command == "stats":
        stats = get_model_stats(metrics_path=metrics_p)
        if args.json_output:
            print(json.dumps(stats, indent=2))
        else:
            print(format_stats(stats))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
