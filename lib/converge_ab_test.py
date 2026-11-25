#!/usr/bin/env python3
"""
converge_ab_test.py -- A/B testing for convergence strategies.

Compare two convergence configurations (strategies, genes, or both) against
the same task using historical converge outcome data. Predicts which
configuration will perform better and reports confidence.

Usage:
    from lib.converge_ab_test import ABTestConfig, run_ab_test, format_result

    config = ABTestConfig(
        task_description="fix auth flow",
        task_type="code",
        strategy_a="add tests first",
        strategy_b="trace full flow first",
    )
    result = run_ab_test(config, history)
    print(format_result(result))

CLI:
    python3 lib/converge_ab_test.py ab-test "fix auth" --strategy-a "add tests" --strategy-b "trace flow"
    python3 lib/converge_ab_test.py ab-test "fix auth" --genes-a converge,test --genes-b converge,trace
    python3 lib/converge_ab_test.py history
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# =========================================================================
# Constants
# =========================================================================

DEFAULT_HISTORY_PATH = _ROOT / "data" / "converge-history.jsonl"
DEFAULT_AB_LOG_PATH = _ROOT / "data" / "ab-test-log.jsonl"
TIE_THRESHOLD = 3  # Score difference <= this is a tie
MIN_HISTORY_FOR_CONFIDENCE = 3  # Need at least this many data points
MAX_CONFIDENCE = 0.95
MIN_CONFIDENCE = 0.1
BASE_SCORE = 50.0  # Default predicted score when no data available
STRATEGY_WEIGHT = 0.6  # How much strategy contributes vs genes
GENE_WEIGHT = 0.4  # How much genes contribute vs strategy


# =========================================================================
# Data structures
# =========================================================================

@dataclass
class ABTestConfig:
    """Configuration for an A/B test comparing two convergence setups."""

    task_description: str
    task_type: str = "code"
    strategy_a: str = ""
    strategy_b: str = ""
    genes_a: list[str] = field(default_factory=list)
    genes_b: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Return list of validation errors, empty if valid."""
        errors = []
        if not self.task_description:
            errors.append("task_description is required")
        if not self.task_type:
            errors.append("task_type is required")
        if not self.strategy_a and not self.genes_a:
            errors.append("strategy_a or genes_a required for variant A")
        if not self.strategy_b and not self.genes_b:
            errors.append("strategy_b or genes_b required for variant B")
        return errors


@dataclass
class ABTestResult:
    """Result of an A/B test comparison."""

    config: ABTestConfig
    predicted_score_a: float
    predicted_score_b: float
    winner: str  # "a", "b", or "tie"
    confidence: float  # 0.0 to 1.0
    reasoning: str
    detail_a: dict[str, Any] = field(default_factory=dict)
    detail_b: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            "task_description": self.config.task_description,
            "task_type": self.config.task_type,
            "strategy_a": self.config.strategy_a,
            "strategy_b": self.config.strategy_b,
            "genes_a": self.config.genes_a,
            "genes_b": self.config.genes_b,
            "predicted_score_a": round(self.predicted_score_a, 2),
            "predicted_score_b": round(self.predicted_score_b, 2),
            "winner": self.winner,
            "confidence": round(self.confidence, 3),
            "reasoning": self.reasoning,
            "detail_a": self.detail_a,
            "detail_b": self.detail_b,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# =========================================================================
# History loading
# =========================================================================

def load_history(path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Load convergence history from JSONL file."""
    p = path or DEFAULT_HISTORY_PATH
    if not p.exists():
        return []
    entries = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed history line")
    return entries


def load_ab_log(path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Load previous AB test results from JSONL log."""
    p = path or DEFAULT_AB_LOG_PATH
    if not p.exists():
        return []
    entries = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed AB log line")
    return entries


def save_ab_result(result: ABTestResult, path: Optional[Path] = None) -> None:
    """Append AB test result to log file."""
    p = path or DEFAULT_AB_LOG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(result.to_dict()) + "\n")


# =========================================================================
# Analysis functions
# =========================================================================

def historical_success_rate(
    strategy: str, task_type: str, history: list[dict[str, Any]]
) -> tuple[float, int]:
    """Find success rate for a strategy on a task type.

    Returns (rate, count) where rate is 0.0-1.0 and count is number of
    matching entries. Returns (0.5, 0) if no matches found.
    """
    if not strategy or not history:
        return 0.5, 0

    strategy_lower = strategy.lower()
    matches = []

    for entry in history:
        entry_type = entry.get("task_type", "")
        if entry_type != task_type:
            continue

        # Check if strategy appears in depths
        depths = entry.get("depths", [])
        for depth in depths:
            depth_strategy = depth.get("strategy", "")
            if strategy_lower in depth_strategy.lower():
                matches.append(entry)
                break

    if not matches:
        return 0.5, 0

    successes = sum(1 for m in matches if m.get("satisfied", False))
    return successes / len(matches), len(matches)


def gene_effectiveness(
    genes: list[str], task_type: str, history: list[dict[str, Any]]
) -> tuple[float, int]:
    """Find average score when these genes were invoked on this task type.

    Returns (avg_score, count). Returns (BASE_SCORE, 0) if no data.
    """
    if not genes or not history:
        return BASE_SCORE, 0

    genes_lower = {g.lower() for g in genes}
    scores = []

    for entry in history:
        if entry.get("task_type", "") != task_type:
            continue

        invoked = {g.lower() for g in entry.get("invoked_genes", [])}
        # Check overlap: at least one gene matches
        if not genes_lower & invoked:
            continue

        # Extract final scores
        final_scores = entry.get("final_dimension_scores", {})
        if final_scores:
            avg = sum(final_scores.values()) / len(final_scores)
            scores.append(avg)
        elif entry.get("satisfied") is not None:
            # No dimension scores, use satisfied as binary
            scores.append(85.0 if entry["satisfied"] else 35.0)

    if not scores:
        return BASE_SCORE, 0

    return sum(scores) / len(scores), len(scores)


def _overlap_ratio(genes: list[str], invoked: list[str]) -> float:
    """Calculate Jaccard-like overlap between gene lists."""
    if not genes and not invoked:
        return 0.0
    genes_set = {g.lower() for g in genes}
    invoked_set = {g.lower() for g in invoked}
    if not genes_set or not invoked_set:
        return 0.0
    intersection = genes_set & invoked_set
    union = genes_set | invoked_set
    return len(intersection) / len(union) if union else 0.0


def predict_outcome(
    task_type: str,
    strategy: str,
    genes: list[str],
    history: list[dict[str, Any]],
) -> tuple[float, dict[str, Any]]:
    """Predict score for a configuration using historical data.

    Returns (predicted_score, detail_dict).
    """
    detail: dict[str, Any] = {}

    # Strategy component
    strat_rate, strat_count = historical_success_rate(strategy, task_type, history)
    strat_score = strat_rate * 100.0
    detail["strategy_success_rate"] = round(strat_rate, 3)
    detail["strategy_sample_count"] = strat_count

    # Gene component
    gene_score, gene_count = gene_effectiveness(genes, task_type, history)
    detail["gene_avg_score"] = round(gene_score, 2)
    detail["gene_sample_count"] = gene_count

    # Weighted combination
    has_strategy = bool(strategy)
    has_genes = bool(genes)

    if has_strategy and has_genes:
        predicted = strat_score * STRATEGY_WEIGHT + gene_score * GENE_WEIGHT
    elif has_strategy:
        predicted = strat_score
    elif has_genes:
        predicted = gene_score
    else:
        predicted = BASE_SCORE

    detail["predicted_score"] = round(predicted, 2)
    return predicted, detail


def determine_winner(
    score_a: float, score_b: float, threshold: float = TIE_THRESHOLD
) -> str:
    """Classify winner based on score difference.

    Returns "a", "b", or "tie".
    """
    diff = score_a - score_b
    if abs(diff) <= threshold:
        return "tie"
    return "a" if diff > 0 else "b"


def _compute_confidence(detail_a: dict, detail_b: dict) -> float:
    """Compute confidence level based on sample sizes."""
    total_samples = (
        detail_a.get("strategy_sample_count", 0)
        + detail_a.get("gene_sample_count", 0)
        + detail_b.get("strategy_sample_count", 0)
        + detail_b.get("gene_sample_count", 0)
    )

    if total_samples == 0:
        return MIN_CONFIDENCE

    # Logarithmic scaling: more data = more confidence, diminishing returns
    raw = math.log1p(total_samples) / math.log1p(50)
    return min(MAX_CONFIDENCE, max(MIN_CONFIDENCE, raw))


def _build_reasoning(
    config: ABTestConfig,
    score_a: float,
    score_b: float,
    detail_a: dict,
    detail_b: dict,
    winner: str,
) -> str:
    """Build human-readable reasoning for the result."""
    parts = []

    # Strategy comparison
    if config.strategy_a and config.strategy_b:
        rate_a = detail_a.get("strategy_success_rate", 0.5)
        rate_b = detail_b.get("strategy_success_rate", 0.5)
        count_a = detail_a.get("strategy_sample_count", 0)
        count_b = detail_b.get("strategy_sample_count", 0)
        parts.append(
            f"Strategy '{config.strategy_a}' success rate: "
            f"{rate_a:.0%} (n={count_a}) vs "
            f"'{config.strategy_b}': {rate_b:.0%} (n={count_b})"
        )

    # Gene comparison
    if config.genes_a and config.genes_b:
        gscore_a = detail_a.get("gene_avg_score", BASE_SCORE)
        gscore_b = detail_b.get("gene_avg_score", BASE_SCORE)
        gcount_a = detail_a.get("gene_sample_count", 0)
        gcount_b = detail_b.get("gene_sample_count", 0)
        parts.append(
            f"Genes {config.genes_a} avg score: "
            f"{gscore_a:.1f} (n={gcount_a}) vs "
            f"{config.genes_b}: {gscore_b:.1f} (n={gcount_b})"
        )

    # Winner summary
    diff = abs(score_a - score_b)
    if winner == "tie":
        parts.append(f"Difference of {diff:.1f} points is within tie threshold ({TIE_THRESHOLD})")
    else:
        label = "A" if winner == "a" else "B"
        parts.append(f"Variant {label} predicted {diff:.1f} points higher")

    # Data warning
    total = (
        detail_a.get("strategy_sample_count", 0)
        + detail_a.get("gene_sample_count", 0)
        + detail_b.get("strategy_sample_count", 0)
        + detail_b.get("gene_sample_count", 0)
    )
    if total < MIN_HISTORY_FOR_CONFIDENCE:
        parts.append(f"Low data warning: only {total} matching history entries")

    return ". ".join(parts) + "."


# =========================================================================
# Main entry point
# =========================================================================

def run_ab_test(
    config: ABTestConfig,
    history: list[dict[str, Any]],
    log_path: Optional[Path] = None,
) -> ABTestResult:
    """Compare two configurations using historical data.

    Args:
        config: The A/B test configuration.
        history: List of historical converge outcome dicts.
        log_path: Optional path to write result log.

    Returns:
        ABTestResult with prediction, winner, confidence, and reasoning.

    Raises:
        ValueError: If config validation fails.
    """
    errors = config.validate()
    if errors:
        raise ValueError(f"Invalid config: {'; '.join(errors)}")

    # Predict both variants
    score_a, detail_a = predict_outcome(
        config.task_type, config.strategy_a, config.genes_a, history
    )
    score_b, detail_b = predict_outcome(
        config.task_type, config.strategy_b, config.genes_b, history
    )

    winner = determine_winner(score_a, score_b)
    confidence = _compute_confidence(detail_a, detail_b)
    reasoning = _build_reasoning(config, score_a, score_b, detail_a, detail_b, winner)

    result = ABTestResult(
        config=config,
        predicted_score_a=score_a,
        predicted_score_b=score_b,
        winner=winner,
        confidence=confidence,
        reasoning=reasoning,
        detail_a=detail_a,
        detail_b=detail_b,
    )

    # Auto-log if path provided
    if log_path is not None:
        save_ab_result(result, log_path)

    return result


# =========================================================================
# Formatting
# =========================================================================

def format_result(result: ABTestResult) -> str:
    """Format AB test result as side-by-side comparison report."""
    lines = []
    lines.append("=" * 60)
    lines.append("CONVERGENCE A/B TEST RESULT")
    lines.append("=" * 60)
    lines.append(f"Task: {result.config.task_description}")
    lines.append(f"Type: {result.config.task_type}")
    lines.append("")

    # Side-by-side
    lines.append(f"{'':>4}{'Variant A':>25}  {'Variant B':>25}")
    lines.append(f"{'':>4}{'-' * 25}  {'-' * 25}")

    if result.config.strategy_a or result.config.strategy_b:
        sa = result.config.strategy_a or "(none)"
        sb = result.config.strategy_b or "(none)"
        lines.append(f"{'Str':>4}{sa:>25}  {sb:>25}")

    if result.config.genes_a or result.config.genes_b:
        ga = ",".join(result.config.genes_a) if result.config.genes_a else "(none)"
        gb = ",".join(result.config.genes_b) if result.config.genes_b else "(none)"
        lines.append(f"{'Gen':>4}{ga:>25}  {gb:>25}")

    lines.append(
        f"{'Pred':>4}{result.predicted_score_a:>25.1f}  "
        f"{result.predicted_score_b:>25.1f}"
    )
    lines.append("")

    # Winner
    if result.winner == "tie":
        winner_text = "TIE (within threshold)"
    elif result.winner == "a":
        winner_text = "VARIANT A"
    else:
        winner_text = "VARIANT B"

    lines.append(f"Winner: {winner_text}")
    lines.append(f"Confidence: {result.confidence:.1%}")
    lines.append("")
    lines.append(f"Reasoning: {result.reasoning}")
    lines.append("=" * 60)

    return "\n".join(lines)


def format_history(results: list[dict[str, Any]]) -> str:
    """Format past AB test results as a summary table."""
    if not results:
        return "No A/B test history found."

    lines = []
    lines.append(f"{'#':>3}  {'Task':>30}  {'Winner':>8}  {'Conf':>6}  {'Score A':>8}  {'Score B':>8}")
    lines.append(f"{'':>3}  {'-' * 30}  {'-' * 8}  {'-' * 6}  {'-' * 8}  {'-' * 8}")

    for i, entry in enumerate(results, 1):
        task = entry.get("task_description", "?")
        if len(task) > 30:
            task = task[:27] + "..."
        winner = entry.get("winner", "?")
        conf = entry.get("confidence", 0)
        sa = entry.get("predicted_score_a", 0)
        sb = entry.get("predicted_score_b", 0)
        lines.append(
            f"{i:>3}  {task:>30}  {winner:>8}  {conf:>6.1%}  {sa:>8.1f}  {sb:>8.1f}"
        )

    lines.append(f"\nTotal: {len(results)} tests")

    # Win stats
    wins_a = sum(1 for r in results if r.get("winner") == "a")
    wins_b = sum(1 for r in results if r.get("winner") == "b")
    ties = sum(1 for r in results if r.get("winner") == "tie")
    lines.append(f"Wins: A={wins_a}, B={wins_b}, Ties={ties}")

    return "\n".join(lines)


# =========================================================================
# CLI
# =========================================================================

def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="A/B test convergence strategies",
        prog="converge_ab_test",
    )
    subparsers = parser.add_subparsers(dest="command")

    # ab-test subcommand
    ab = subparsers.add_parser("ab-test", help="Run an A/B test")
    ab.add_argument("task", help="Task description")
    ab.add_argument("--task-type", default="code", help="Task type (default: code)")
    ab.add_argument("--strategy-a", default="", help="Strategy for variant A")
    ab.add_argument("--strategy-b", default="", help="Strategy for variant B")
    ab.add_argument("--genes-a", default="", help="Comma-separated genes for variant A")
    ab.add_argument("--genes-b", default="", help="Comma-separated genes for variant B")
    ab.add_argument("--history-file", default=None, help="Path to history JSONL")
    ab.add_argument("--log-file", default=None, help="Path to AB test log")

    # history subcommand
    subparsers.add_parser("history", help="Show past A/B test results")

    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    args = _parse_args(argv)

    if args.command == "ab-test":
        genes_a = [g.strip() for g in args.genes_a.split(",") if g.strip()] if args.genes_a else []
        genes_b = [g.strip() for g in args.genes_b.split(",") if g.strip()] if args.genes_b else []

        config = ABTestConfig(
            task_description=args.task,
            task_type=args.task_type,
            strategy_a=args.strategy_a,
            strategy_b=args.strategy_b,
            genes_a=genes_a,
            genes_b=genes_b,
        )

        errors = config.validate()
        if errors:
            print(f"Error: {'; '.join(errors)}", file=sys.stderr)
            return 1

        history_path = Path(args.history_file) if args.history_file else None
        log_path = Path(args.log_file) if args.log_file else None

        history = load_history(history_path)
        result = run_ab_test(config, history, log_path=log_path)
        print(format_result(result))
        return 0

    elif args.command == "history":
        results = load_ab_log()
        print(format_history(results))
        return 0

    else:
        print("Usage: converge_ab_test {ab-test|history}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
