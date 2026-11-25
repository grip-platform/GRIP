#!/usr/bin/env python3
"""converge_meta.py -- Meta-analysis of the convergence system itself.

Analyses convergence performance over time: is the system improving at
converging? Has improvement plateaued? Is the genome evolving toward
better genes? Is the forecaster calibrated?

This is the "convergence of convergence" -- a self-referential quality
signal that treats the converge engine as its own subject.

History format: standard converge-metrics JSONL (same as converge_metrics.py).
Genome format: [{"generation": N, "genes": [{"name": "...", "fitness": 0.X, "status": "active"}]}]

Usage:
    from lib.converge_meta import (
        analyze_improvement, detect_plateau, assess_genome_health,
        assess_forecast_accuracy, compute_meta_score, format_meta_report,
    )

    report = compute_meta_score(runs)
    print(format_meta_report(report))

CLI:
    python3 lib/converge_meta.py meta              # Full meta-report
    python3 lib/converge_meta.py improving          # Is the system improving?
    python3 lib/converge_meta.py plateau            # Has improvement stalled?
    python3 lib/converge_meta.py forecast-accuracy  # Is the forecaster calibrated?
    python3 lib/converge_meta.py genome-health      # Is the genome healthy?
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class MetaTrend:
    """A trend line for a single metric over time."""

    metric_name: str
    values: list[float] = field(default_factory=list)
    trend: str = "stable"  # improving, declining, stable
    confidence: float = 0.0


@dataclass
class MetaReport:
    """Overall meta-analysis of the convergence system."""

    is_improving: bool = False
    improvement_rate: float = 0.0
    plateau_detected: bool = False
    forecast_accuracy: float = 0.0
    genome_health: float = 0.0
    overall_assessment: str = "insufficient data"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_final_score(run: dict) -> Optional[float]:
    """Extract a single aggregate score from a converge run."""
    # Try final_score first (agent-driven recording)
    if "final_score" in run and isinstance(run["final_score"], (int, float)):
        return float(run["final_score"])
    # Try final_dimension_scores (forecast/history format)
    dim_scores = run.get("final_dimension_scores")
    if dim_scores and isinstance(dim_scores, dict):
        vals = [v for v in dim_scores.values() if isinstance(v, (int, float))]
        if vals:
            return sum(vals) / len(vals)
    # Try score directly
    if "score" in run and isinstance(run["score"], (int, float)):
        return float(run["score"])
    return None


def _extract_depth(run: dict) -> Optional[int]:
    """Extract depth reached from a converge run."""
    d = run.get("depth_reached")
    if isinstance(d, int):
        return d
    return None


def _extract_satisfied(run: dict) -> Optional[bool]:
    """Extract satisfaction flag from a converge run."""
    s = run.get("satisfied")
    if isinstance(s, bool):
        return s
    return None


def _linear_regression(values: list[float]) -> tuple[float, float]:
    """Simple OLS regression: slope and R-squared.

    Returns (slope, r_squared). Uses indices as x values.
    """
    n = len(values)
    if n < 2:
        return 0.0, 0.0

    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n

    ss_xy = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    ss_xx = sum((i - x_mean) ** 2 for i in range(n))
    ss_yy = sum((v - y_mean) ** 2 for v in values)

    if ss_xx == 0:
        return 0.0, 0.0

    slope = ss_xy / ss_xx

    if ss_yy == 0:
        r_squared = 1.0 if slope == 0 else 0.0
    else:
        r_squared = (ss_xy ** 2) / (ss_xx * ss_yy)

    return slope, r_squared


def _classify_trend(slope: float, r_squared: float, threshold: float = 0.01) -> str:
    """Classify a trend as improving, declining, or stable."""
    if r_squared < 0.1:
        return "stable"  # Not enough signal
    if slope > threshold:
        return "improving"
    if slope < -threshold:
        return "declining"
    return "stable"


def _windowed_averages(values: list[float], window: int) -> list[float]:
    """Compute moving averages with the given window size."""
    if not values or window < 1:
        return []
    result = []
    for i in range(len(values) - window + 1):
        w = values[i : i + window]
        result.append(sum(w) / len(w))
    return result


# ---------------------------------------------------------------------------
# Core analysis functions
# ---------------------------------------------------------------------------


def compute_score_trend(runs: list[dict]) -> MetaTrend:
    """Analyse score trajectory over time."""
    scores = []
    for run in runs:
        s = _extract_final_score(run)
        if s is not None:
            scores.append(s)

    if len(scores) < 2:
        return MetaTrend(metric_name="score", values=scores, trend="stable", confidence=0.0)

    slope, r_sq = _linear_regression(scores)
    trend = _classify_trend(slope, r_sq)

    return MetaTrend(metric_name="score", values=scores, trend=trend, confidence=round(r_sq, 4))


def compute_depth_trend(runs: list[dict]) -> MetaTrend:
    """Analyse depth trajectory over time (lower = faster convergence)."""
    depths = []
    for run in runs:
        d = _extract_depth(run)
        if d is not None:
            depths.append(float(d))

    if len(depths) < 2:
        return MetaTrend(metric_name="depth", values=depths, trend="stable", confidence=0.0)

    slope, r_sq = _linear_regression(depths)
    # Invert: negative slope (fewer depths needed) = improving
    if r_sq < 0.1:
        trend = "stable"
    elif slope < -0.01:
        trend = "improving"
    elif slope > 0.01:
        trend = "declining"
    else:
        trend = "stable"

    return MetaTrend(metric_name="depth", values=depths, trend=trend, confidence=round(r_sq, 4))


def compute_satisfaction_trend(runs: list[dict], window: int = 5) -> MetaTrend:
    """Analyse satisfaction rate over time using a moving window."""
    satisfied_flags = []
    for run in runs:
        s = _extract_satisfied(run)
        if s is not None:
            satisfied_flags.append(1.0 if s else 0.0)

    if len(satisfied_flags) < window:
        avg = sum(satisfied_flags) / len(satisfied_flags) if satisfied_flags else 0.0
        return MetaTrend(
            metric_name="satisfaction",
            values=[avg] if satisfied_flags else [],
            trend="stable",
            confidence=0.0,
        )

    windowed = _windowed_averages(satisfied_flags, window)
    slope, r_sq = _linear_regression(windowed)
    trend = _classify_trend(slope, r_sq, threshold=0.005)

    return MetaTrend(
        metric_name="satisfaction",
        values=[round(v, 4) for v in windowed],
        trend=trend,
        confidence=round(r_sq, 4),
    )


def analyze_improvement(runs: list[dict]) -> dict:
    """Determine whether the convergence system is improving over time.

    Returns a dict with:
        is_improving: bool
        rate: float (slope of score trend, points per run)
        score_trend: MetaTrend
        depth_trend: MetaTrend
        satisfaction_trend: MetaTrend
        signals: list of human-readable signals
    """
    score_trend = compute_score_trend(runs)
    depth_trend = compute_depth_trend(runs)
    sat_trend = compute_satisfaction_trend(runs)

    signals = []
    improving_count = 0
    total_signals = 0

    for t in [score_trend, depth_trend, sat_trend]:
        total_signals += 1
        if t.trend == "improving":
            improving_count += 1
            signals.append(f"{t.metric_name}: improving (confidence {t.confidence:.2f})")
        elif t.trend == "declining":
            signals.append(f"{t.metric_name}: declining (confidence {t.confidence:.2f})")
        else:
            signals.append(f"{t.metric_name}: stable")

    is_improving = improving_count >= 2 or (
        improving_count >= 1
        and all(t.trend != "declining" for t in [score_trend, depth_trend, sat_trend])
    )

    rate = 0.0
    if score_trend.values and len(score_trend.values) >= 2:
        slope, _ = _linear_regression(score_trend.values)
        rate = round(slope, 4)

    return {
        "is_improving": is_improving,
        "rate": rate,
        "score_trend": score_trend,
        "depth_trend": depth_trend,
        "satisfaction_trend": sat_trend,
        "signals": signals,
    }


def detect_plateau(runs: list[dict], window: int = 10) -> dict:
    """Detect whether improvement has stalled.

    Compares the last `window` runs against the previous `window` runs.
    If score change is < 2 points and satisfaction change < 5%, plateau detected.

    Returns a dict with:
        plateau_detected: bool
        recent_avg_score: float
        previous_avg_score: float
        score_delta: float
        recent_satisfaction: float
        previous_satisfaction: float
        satisfaction_delta: float
    """
    scores = [_extract_final_score(r) for r in runs]
    scores = [s for s in scores if s is not None]

    satisfied = [_extract_satisfied(r) for r in runs]
    satisfied = [s for s in satisfied if s is not None]

    result = {
        "plateau_detected": False,
        "recent_avg_score": 0.0,
        "previous_avg_score": 0.0,
        "score_delta": 0.0,
        "recent_satisfaction": 0.0,
        "previous_satisfaction": 0.0,
        "satisfaction_delta": 0.0,
    }

    if len(scores) < window * 2:
        # Not enough data to detect plateau
        return result

    recent_scores = scores[-window:]
    previous_scores = scores[-window * 2 : -window]

    recent_avg = sum(recent_scores) / len(recent_scores)
    previous_avg = sum(previous_scores) / len(previous_scores)
    score_delta = recent_avg - previous_avg

    result["recent_avg_score"] = round(recent_avg, 2)
    result["previous_avg_score"] = round(previous_avg, 2)
    result["score_delta"] = round(score_delta, 2)

    if len(satisfied) >= window * 2:
        recent_sat = satisfied[-window:]
        previous_sat = satisfied[-window * 2 : -window]
        recent_sat_rate = sum(1 for s in recent_sat if s) / len(recent_sat)
        previous_sat_rate = sum(1 for s in previous_sat if s) / len(previous_sat)
        sat_delta = recent_sat_rate - previous_sat_rate

        result["recent_satisfaction"] = round(recent_sat_rate, 4)
        result["previous_satisfaction"] = round(previous_sat_rate, 4)
        result["satisfaction_delta"] = round(sat_delta, 4)

        # Plateau: both score and satisfaction are flat
        result["plateau_detected"] = abs(score_delta) < 2.0 and abs(sat_delta) < 0.05
    else:
        result["plateau_detected"] = abs(score_delta) < 2.0

    return result


def assess_genome_health(genome_history: list[dict]) -> dict:
    """Assess genome evolution quality.

    Analyses:
        - diversity: number of unique active genes across generations
        - fitness_trend: are gene fitness values improving?
        - turnover: rate of gene birth/death (healthy = moderate)
        - active_ratio: fraction of genes that are active

    Args:
        genome_history: List of generation snapshots, each with:
            {"generation": N, "genes": [{"name": "...", "fitness": 0.X, "status": "active|dormant|dead"}]}

    Returns dict with:
        health: float (0-1, overall genome health)
        diversity: float (0-1)
        fitness_trend: MetaTrend
        active_ratio: float
        turnover_rate: float
        generation_count: int
        signals: list of str
    """
    if not genome_history:
        return {
            "health": 0.0,
            "diversity": 0.0,
            "fitness_trend": MetaTrend(metric_name="genome_fitness"),
            "active_ratio": 0.0,
            "turnover_rate": 0.0,
            "generation_count": 0,
            "signals": ["no genome history available"],
        }

    signals = []
    generation_count = len(genome_history)

    # Diversity: unique active gene names across all generations
    all_active_genes: set[str] = set()
    per_gen_active: list[int] = []
    per_gen_fitness: list[float] = []
    per_gen_total: list[int] = []

    for gen in genome_history:
        genes = gen.get("genes", [])
        active = [g for g in genes if g.get("status") == "active"]
        all_active_genes.update(g.get("name", "") for g in active)
        per_gen_active.append(len(active))
        per_gen_total.append(len(genes))

        fitnesses = [g.get("fitness", 0.0) for g in active if isinstance(g.get("fitness"), (int, float))]
        if fitnesses:
            per_gen_fitness.append(sum(fitnesses) / len(fitnesses))

    # Diversity score: log-scaled, 10+ unique genes = perfect diversity
    diversity = min(1.0, math.log(len(all_active_genes) + 1) / math.log(11))

    # Active ratio (from latest generation)
    if per_gen_total and per_gen_total[-1] > 0:
        active_ratio = per_gen_active[-1] / per_gen_total[-1]
    else:
        active_ratio = 0.0

    # Fitness trend
    if len(per_gen_fitness) >= 2:
        slope, r_sq = _linear_regression(per_gen_fitness)
        ft_trend = _classify_trend(slope, r_sq, threshold=0.005)
        fitness_trend = MetaTrend(
            metric_name="genome_fitness",
            values=[round(v, 4) for v in per_gen_fitness],
            trend=ft_trend,
            confidence=round(r_sq, 4),
        )
    else:
        fitness_trend = MetaTrend(
            metric_name="genome_fitness",
            values=[round(v, 4) for v in per_gen_fitness],
        )

    # Turnover rate: how many genes changed status between consecutive generations
    turnover_events = 0
    for i in range(1, len(genome_history)):
        prev_names = {g.get("name") for g in genome_history[i - 1].get("genes", [])}
        curr_names = {g.get("name") for g in genome_history[i].get("genes", [])}
        added = curr_names - prev_names
        removed = prev_names - curr_names
        turnover_events += len(added) + len(removed)

    turnover_rate = turnover_events / max(1, generation_count - 1) if generation_count > 1 else 0.0

    # Signals
    if diversity > 0.7:
        signals.append("high gene diversity")
    elif diversity < 0.3:
        signals.append("low gene diversity -- genome may be too narrow")

    if fitness_trend.trend == "improving":
        signals.append("gene fitness improving over generations")
    elif fitness_trend.trend == "declining":
        signals.append("gene fitness declining -- possible regression")

    if active_ratio < 0.5:
        signals.append("low active ratio -- many genes dormant or dead")
    elif active_ratio > 0.8:
        signals.append("healthy active ratio")

    # Moderate turnover (0.5-3.0 per generation) is healthy
    if turnover_rate > 5.0:
        signals.append("excessive turnover -- genome unstable")
    elif turnover_rate < 0.1 and generation_count > 3:
        signals.append("no turnover -- genome may be stagnant")
    elif 0.1 <= turnover_rate <= 5.0:
        signals.append("healthy turnover rate")

    if not signals:
        signals.append("genome within normal parameters")

    # Overall health: weighted combination
    fitness_score = 0.5  # neutral default
    if per_gen_fitness:
        latest_fitness = per_gen_fitness[-1]
        fitness_score = min(1.0, latest_fitness)  # fitness is already 0-1

    health = round(
        0.30 * diversity
        + 0.30 * fitness_score
        + 0.20 * active_ratio
        + 0.20 * min(1.0, 1.0 - abs(turnover_rate - 1.5) / 5.0),  # optimal turnover ~1.5
        4,
    )
    health = max(0.0, min(1.0, health))

    return {
        "health": health,
        "diversity": round(diversity, 4),
        "fitness_trend": fitness_trend,
        "active_ratio": round(active_ratio, 4),
        "turnover_rate": round(turnover_rate, 4),
        "generation_count": generation_count,
        "signals": signals,
    }


def assess_forecast_accuracy(
    forecasts: list[dict], actuals: list[dict]
) -> dict:
    """Assess forecaster calibration using Brier-like scoring.

    Each forecast should have: {"predicted_score": float, "predicted_depth": int}
    Each actual should have: {"final_score": float, "depth_reached": int}
    The lists are paired by index.

    Returns:
        accuracy: float (0-1, 1=perfect)
        score_mae: float (mean absolute error for scores)
        depth_mae: float (mean absolute error for depths)
        brier_score: float (lower=better, 0=perfect)
        trend: MetaTrend (is accuracy improving over time?)
        n_pairs: int
    """
    n = min(len(forecasts), len(actuals))

    if n == 0:
        return {
            "accuracy": 0.0,
            "score_mae": 0.0,
            "depth_mae": 0.0,
            "brier_score": 1.0,
            "trend": MetaTrend(metric_name="forecast_accuracy"),
            "n_pairs": 0,
        }

    score_errors = []
    depth_errors = []
    brier_components = []

    for i in range(n):
        pred_score = forecasts[i].get("predicted_score", 0.0)
        actual_score_val = _extract_final_score(actuals[i])
        if actual_score_val is None:
            continue

        score_err = abs(pred_score - actual_score_val)
        score_errors.append(score_err)

        # Brier: normalize to 0-1 range
        brier = ((pred_score - actual_score_val) / 100.0) ** 2
        brier_components.append(brier)

        pred_depth = forecasts[i].get("predicted_depth", 0)
        actual_depth = _extract_depth(actuals[i])
        if actual_depth is not None:
            depth_errors.append(abs(pred_depth - actual_depth))

    if not score_errors:
        return {
            "accuracy": 0.0,
            "score_mae": 0.0,
            "depth_mae": 0.0,
            "brier_score": 1.0,
            "trend": MetaTrend(metric_name="forecast_accuracy"),
            "n_pairs": 0,
        }

    score_mae = sum(score_errors) / len(score_errors)
    depth_mae = sum(depth_errors) / len(depth_errors) if depth_errors else 0.0
    brier_score = sum(brier_components) / len(brier_components)

    # Accuracy: inverse of normalized MAE (MAE of 0 = accuracy 1.0, MAE of 50 = accuracy 0.0)
    accuracy = max(0.0, 1.0 - score_mae / 50.0)

    # Trend: are per-run accuracies improving?
    per_run_accuracy = [max(0.0, 1.0 - e / 50.0) for e in score_errors]
    if len(per_run_accuracy) >= 2:
        slope, r_sq = _linear_regression(per_run_accuracy)
        trend_dir = _classify_trend(slope, r_sq, threshold=0.005)
        trend = MetaTrend(
            metric_name="forecast_accuracy",
            values=[round(v, 4) for v in per_run_accuracy],
            trend=trend_dir,
            confidence=round(r_sq, 4),
        )
    else:
        trend = MetaTrend(
            metric_name="forecast_accuracy",
            values=[round(v, 4) for v in per_run_accuracy],
        )

    return {
        "accuracy": round(accuracy, 4),
        "score_mae": round(score_mae, 2),
        "depth_mae": round(depth_mae, 2),
        "brier_score": round(brier_score, 4),
        "trend": trend,
        "n_pairs": len(score_errors),
    }


def compute_meta_score(runs: list[dict], genome_history: Optional[list[dict]] = None,
                       forecasts: Optional[list[dict]] = None,
                       actuals: Optional[list[dict]] = None) -> MetaReport:
    """Compute the overall 'convergence of convergence' score.

    Combines all meta-analysis signals into a single MetaReport.
    """
    if not runs:
        return MetaReport()

    improvement = analyze_improvement(runs)
    plateau = detect_plateau(runs)
    genome = assess_genome_health(genome_history or [])
    forecast = assess_forecast_accuracy(forecasts or [], actuals or [])

    # Determine overall assessment
    assessments = []

    if improvement["is_improving"]:
        assessments.append("system is improving")
    else:
        # Check if it's at least stable with high scores
        scores = [_extract_final_score(r) for r in runs]
        scores = [s for s in scores if s is not None]
        if scores and sum(scores) / len(scores) > 80:
            assessments.append("system is performing well (stable)")
        else:
            assessments.append("system is not improving")

    if plateau["plateau_detected"]:
        assessments.append("improvement has plateaued")

    if genome["health"] > 0.7:
        assessments.append("genome is healthy")
    elif genome["health"] > 0.0:
        if genome["health"] < 0.4:
            assessments.append("genome needs attention")

    if forecast["accuracy"] > 0.7:
        assessments.append("forecaster is well-calibrated")
    elif forecast["n_pairs"] > 0 and forecast["accuracy"] < 0.4:
        assessments.append("forecaster needs recalibration")

    return MetaReport(
        is_improving=improvement["is_improving"],
        improvement_rate=improvement["rate"],
        plateau_detected=plateau["plateau_detected"],
        forecast_accuracy=forecast["accuracy"],
        genome_health=genome["health"],
        overall_assessment="; ".join(assessments) if assessments else "insufficient data",
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_trend(trend: MetaTrend) -> str:
    """Format a MetaTrend as an ASCII trend line.

    Example: "score: 72 -> 75 -> 80 -> 85 [improving, conf=0.92]"
    """
    if not trend.values:
        return f"{trend.metric_name}: no data"

    # Show up to 8 values, sampled evenly if more
    vals = trend.values
    if len(vals) > 8:
        step = len(vals) / 8
        indices = [int(i * step) for i in range(8)]
        # Always include the last value
        if indices[-1] != len(vals) - 1:
            indices[-1] = len(vals) - 1
        vals = [vals[i] for i in indices]

    formatted_vals = []
    for v in vals:
        if isinstance(v, float) and v == int(v):
            formatted_vals.append(str(int(v)))
        elif isinstance(v, float):
            formatted_vals.append(f"{v:.1f}")
        else:
            formatted_vals.append(str(v))

    arrow_line = " -> ".join(formatted_vals)

    conf_str = f", conf={trend.confidence:.2f}" if trend.confidence > 0 else ""
    return f"{trend.metric_name}: {arrow_line} [{trend.trend}{conf_str}]"


def format_meta_report(report: MetaReport) -> str:
    """Format a MetaReport as a human-readable summary."""
    lines = [
        "--- Convergence Meta-Analysis ---",
        "",
        f"Improving:           {'yes' if report.is_improving else 'no'}",
        f"Improvement Rate:    {report.improvement_rate:+.4f} points/run",
        f"Plateau Detected:    {'yes' if report.plateau_detected else 'no'}",
        f"Forecast Accuracy:   {report.forecast_accuracy:.0%}",
        f"Genome Health:       {report.genome_health:.0%}",
        "",
        f"Assessment: {report.overall_assessment}",
        "",
        "---",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSONL loading
# ---------------------------------------------------------------------------


def _load_jsonl(path: Path) -> list[dict]:
    """Load entries from a JSONL file."""
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


def _load_json(path: Path) -> list[dict]:
    """Load a JSON array file."""
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, ValueError):
        return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    import os

    CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

    parser = argparse.ArgumentParser(
        prog="converge-meta",
        description="Meta-analysis of the convergence system itself.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="meta",
        choices=["meta", "improving", "plateau", "forecast-accuracy", "genome-health"],
        help="Analysis to run (default: meta)",
    )
    parser.add_argument("--history", help="Path to converge-metrics JSONL")
    parser.add_argument("--genome", help="Path to genome history JSON")
    parser.add_argument("--forecasts", help="Path to forecasts JSONL")
    parser.add_argument("--actuals", help="Path to actuals JSONL")
    parser.add_argument("--window", type=int, default=10, help="Window size for plateau detection")

    args = parser.parse_args(argv)

    # Load data
    history_path = Path(args.history) if args.history else CLAUDE_DIR / "learning" / "converge-metrics.jsonl"
    runs = _load_jsonl(history_path)

    # Filter to completed runs (have final_score or final_dimension_scores)
    runs = [r for r in runs if _extract_final_score(r) is not None]

    if args.command == "meta":
        genome_history = _load_json(Path(args.genome)) if args.genome else []
        forecasts = _load_jsonl(Path(args.forecasts)) if args.forecasts else []
        actuals = _load_jsonl(Path(args.actuals)) if args.actuals else []

        report = compute_meta_score(runs, genome_history, forecasts, actuals)
        print(format_meta_report(report))

        # Also print trends if data available
        if runs:
            score_trend = compute_score_trend(runs)
            depth_trend = compute_depth_trend(runs)
            sat_trend = compute_satisfaction_trend(runs)
            print(format_trend(score_trend))
            print(format_trend(depth_trend))
            print(format_trend(sat_trend))

    elif args.command == "improving":
        result = analyze_improvement(runs)
        status = "YES" if result["is_improving"] else "NO"
        print(f"System improving: {status} (rate: {result['rate']:+.4f} points/run)")
        for sig in result["signals"]:
            print(f"  - {sig}")

    elif args.command == "plateau":
        result = detect_plateau(runs, window=args.window)
        if result["plateau_detected"]:
            print(f"Plateau DETECTED")
            print(f"  Recent avg:   {result['recent_avg_score']:.1f}")
            print(f"  Previous avg: {result['previous_avg_score']:.1f}")
            print(f"  Delta:        {result['score_delta']:+.1f}")
        elif len(runs) < args.window * 2:
            print(f"Not enough data ({len(runs)} runs, need {args.window * 2})")
        else:
            print(f"No plateau detected")
            print(f"  Score delta:        {result['score_delta']:+.1f}")
            print(f"  Satisfaction delta: {result['satisfaction_delta']:+.4f}")

    elif args.command == "forecast-accuracy":
        forecasts = _load_jsonl(Path(args.forecasts)) if args.forecasts else []
        actuals = _load_jsonl(Path(args.actuals)) if args.actuals else []
        result = assess_forecast_accuracy(forecasts, actuals)
        print(f"Forecast accuracy: {result['accuracy']:.0%}")
        print(f"  Score MAE:   {result['score_mae']:.1f}")
        print(f"  Depth MAE:   {result['depth_mae']:.1f}")
        print(f"  Brier score: {result['brier_score']:.4f}")
        print(f"  Pairs:       {result['n_pairs']}")
        if result["trend"].values:
            print(f"  {format_trend(result['trend'])}")

    elif args.command == "genome-health":
        genome_history = _load_json(Path(args.genome)) if args.genome else []
        result = assess_genome_health(genome_history)
        print(f"Genome health: {result['health']:.0%}")
        print(f"  Diversity:    {result['diversity']:.2f}")
        print(f"  Active ratio: {result['active_ratio']:.2f}")
        print(f"  Turnover:     {result['turnover_rate']:.2f}/gen")
        print(f"  Generations:  {result['generation_count']}")
        for sig in result["signals"]:
            print(f"  - {sig}")
        if result["fitness_trend"].values:
            print(f"  {format_trend(result['fitness_trend'])}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
