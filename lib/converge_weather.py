#!/usr/bin/env python3
"""
converge_weather.py -- Convergence weather report.

Generates a metaphorical "weather report" for convergence conditions by
combining signals from contraction_fitness, converge_anomaly, and
converge_predictor. The result is a human-friendly status dashboard
that communicates convergence health at a glance.

Weather conditions:
  Clear Skies   -- all recent runs contractive, no anomalies, high scores
  Partly Cloudy -- mixed signals, minor anomalies
  Overcast      -- mostly non-contractive, plateau patterns
  Stormy        -- high-severity anomalies, diverging trajectories
  Fog           -- insufficient data or dimension_collapse hiding quality
  Perfect Storm -- 3+ high-severity anomalies AND diverging trajectories

Metrics mapping:
  Temperature = avg score of last N runs (higher = warmer)
  Wind Speed  = avg velocity across last N runs (higher = faster improvement)
  Pressure    = avg contraction ratio (lower = more stable convergence)
  Visibility  = 1 - anomaly_rate (higher = cleaner data)

Usage:
    python3 lib/converge_weather.py forecast
    python3 lib/converge_weather.py forecast --json
    python3 lib/converge_weather.py forecast --max-runs 20
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DEFAULT_METRICS_PATH = CLAUDE_DIR / "learning" / "converge-metrics.jsonl"

# ---------------------------------------------------------------------------
# Optional imports with graceful fallback
# ---------------------------------------------------------------------------

try:
    from lib.contraction_fitness import (
        convergence_quality,
        is_contractive,
        _load_runs as cf_load_runs,
        _extract_trajectory,
        ConvergenceQuality,
    )
    HAS_CONTRACTION_FITNESS = True
except ImportError:
    HAS_CONTRACTION_FITNESS = False

try:
    from lib.converge_anomaly import (
        load_metrics as anomaly_load_metrics,
        detect_anomalies,
        severity_filter,
        AnomalyReport,
        Anomaly,
    )
    HAS_CONVERGE_ANOMALY = True
except ImportError:
    HAS_CONVERGE_ANOMALY = False

try:
    from lib.converge_predictor import (
        predict_success,
        PredictionResult,
    )
    HAS_CONVERGE_PREDICTOR = True
except ImportError:
    HAS_CONVERGE_PREDICTOR = False


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class WeatherReport:
    """Convergence weather report."""

    condition: str  # Clear Skies, Partly Cloudy, Overcast, Stormy, Fog, Perfect Storm
    temperature: float  # avg recent score (0-100)
    wind_speed: float  # avg velocity (score gain per depth)
    pressure: float  # avg contraction ratio (lower = more stable)
    visibility: float  # 1 - anomaly_rate (0-1)
    anomaly_count: int  # total anomalies detected
    forecast: str  # predicted outlook as weather metaphor
    confidence: float  # 0-1 overall confidence
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal analysis helpers
# ---------------------------------------------------------------------------

def _load_recent_runs(
    metrics_path: Optional[Path] = None,
    max_runs: int = 10,
) -> list[dict]:
    """Load the most recent converge runs from the JSONL file.

    Uses contraction_fitness's loader if available, otherwise falls
    back to a minimal inline parser.
    """
    path = metrics_path or DEFAULT_METRICS_PATH
    if not path.exists():
        return []

    if HAS_CONTRACTION_FITNESS:
        all_runs = cf_load_runs(path)
        # Sort by timestamp (most recent first) and take max_runs
        sorted_runs = sorted(
            all_runs.values(),
            key=lambda r: r.get("timestamp", ""),
            reverse=True,
        )
        return sorted_runs[:max_runs]

    # Fallback: minimal parser
    events: dict[str, dict] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = evt.get("run_id")
            if not rid:
                continue
            if rid not in events:
                events[rid] = {"run_id": rid, "depths": [], "timestamp": ""}
            etype = evt.get("event", "")
            if etype == "converge_start":
                events[rid]["task"] = evt.get("task", "")
                events[rid]["task_type"] = evt.get("task_type", "general")
                events[rid]["timestamp"] = evt.get("timestamp", "")
            elif etype == "converge_depth":
                events[rid]["depths"].append(evt)
            elif etype == "converge_end":
                events[rid]["final_score"] = evt.get("final_score")
                events[rid]["satisfied"] = evt.get("satisfied", False)
                events[rid]["depth_reached"] = evt.get("depth_reached", 0)
                events[rid]["velocity_trajectory"] = evt.get("velocity_trajectory", [])

    sorted_runs = sorted(
        events.values(),
        key=lambda r: r.get("timestamp", ""),
        reverse=True,
    )
    return sorted_runs[:max_runs]


def _extract_scores(runs: list[dict]) -> list[float]:
    """Extract final scores from runs."""
    scores = []
    for run in runs:
        fs = run.get("final_score")
        if fs is not None and isinstance(fs, (int, float)):
            scores.append(float(fs))
    return scores


def _extract_trajectories(runs: list[dict]) -> list[list[float]]:
    """Extract score trajectories from runs."""
    trajectories = []
    for run in runs:
        depths = run.get("depths", [])
        if depths:
            traj = [d.get("score", 0) for d in depths if d.get("score") is not None]
            if traj:
                trajectories.append(traj)
        elif "velocity_trajectory" in run:
            vt = run.get("velocity_trajectory", [])
            if vt:
                trajectories.append(vt)
    return trajectories


def _analyze_trajectories(runs: list[dict]) -> dict[str, Any]:
    """Analyze trajectory health using contraction_fitness.

    Returns:
        Dict with keys: contractive_count, total, avg_ratio, trajectory_types,
        all_contractive, has_diverging.
    """
    result: dict[str, Any] = {
        "contractive_count": 0,
        "total": 0,
        "avg_ratio": 0.0,
        "trajectory_types": [],
        "all_contractive": False,
        "has_diverging": False,
    }

    if not runs:
        return result

    trajectories = _extract_trajectories(runs)
    if not trajectories:
        return result

    ratios = []
    contractive_count = 0
    traj_types = []

    for traj in trajectories:
        if len(traj) < 3:
            continue

        if HAS_CONTRACTION_FITNESS:
            contractive, ratio = is_contractive(traj)
            quality = convergence_quality(traj)
            traj_types.append(quality.trajectory_type)
            ratios.append(ratio)
            if contractive:
                contractive_count += 1
        else:
            # Minimal fallback: check if scores are generally increasing
            # with decreasing deltas
            deltas = [abs(traj[i] - traj[i - 1]) for i in range(1, len(traj))]
            if len(deltas) >= 2:
                decreasing = sum(
                    1 for i in range(1, len(deltas))
                    if deltas[i] < deltas[i - 1]
                )
                contractive = decreasing > len(deltas) / 2
                if contractive:
                    contractive_count += 1
                avg_r = sum(
                    deltas[i] / deltas[i - 1]
                    for i in range(1, len(deltas))
                    if deltas[i - 1] > 0
                ) / max(len(deltas) - 1, 1)
                ratios.append(avg_r)

    total = len(trajectories)
    result["contractive_count"] = contractive_count
    result["total"] = total
    result["avg_ratio"] = sum(ratios) / len(ratios) if ratios else 0.0
    result["trajectory_types"] = traj_types
    result["all_contractive"] = (contractive_count == total and total > 0)
    result["has_diverging"] = "diverging" in traj_types

    return result


def _analyze_anomalies(runs: list[dict], metrics_path: Optional[Path] = None) -> dict[str, Any]:
    """Analyze anomaly health using converge_anomaly.

    Returns:
        Dict with keys: total_anomalies, high_severity_count, anomaly_rate,
        has_dimension_collapse, anomaly_types.
    """
    result: dict[str, Any] = {
        "total_anomalies": 0,
        "high_severity_count": 0,
        "anomaly_rate": 0.0,
        "has_dimension_collapse": False,
        "anomaly_types": [],
    }

    if not HAS_CONVERGE_ANOMALY:
        return result

    path = metrics_path or DEFAULT_METRICS_PATH
    if not path.exists():
        return result

    loaded_runs = anomaly_load_metrics(path)
    if not loaded_runs:
        return result

    # Filter to the run_ids we care about
    target_ids = {r.get("run_id") for r in runs if r.get("run_id")}
    if target_ids:
        filtered = [r for r in loaded_runs if r.get("run_id") in target_ids]
    else:
        filtered = loaded_runs

    report = detect_anomalies(filtered)
    high = severity_filter(report.anomalies, "high")
    types = [a.anomaly_type for a in report.anomalies]

    result["total_anomalies"] = len(report.anomalies)
    result["high_severity_count"] = len(high)
    result["anomaly_rate"] = report.anomaly_rate
    result["has_dimension_collapse"] = "DIMENSION_COLLAPSE" in types
    result["anomaly_types"] = list(set(types))

    return result


def _classify_condition(
    trajectory_data: dict[str, Any],
    anomaly_data: dict[str, Any],
    scores: list[float],
) -> str:
    """Classify the weather condition based on combined analysis.

    Priority order (highest to lowest):
    1. Perfect Storm — 3+ high-severity AND diverging
    2. Fog — insufficient data OR dimension_collapse
    3. Stormy — multiple high-severity anomalies OR diverging
    4. Overcast — non-contractive majority, plateau patterns
    5. Partly Cloudy — mixed signals
    6. Clear Skies — healthy across all dimensions
    """
    total_trajectories = trajectory_data.get("total", 0)
    contractive_count = trajectory_data.get("contractive_count", 0)
    all_contractive = trajectory_data.get("all_contractive", False)
    has_diverging = trajectory_data.get("has_diverging", False)
    traj_types = trajectory_data.get("trajectory_types", [])

    high_severity = anomaly_data.get("high_severity_count", 0)
    total_anomalies = anomaly_data.get("total_anomalies", 0)
    has_dimension_collapse = anomaly_data.get("has_dimension_collapse", False)

    avg_score = sum(scores) / len(scores) if scores else 0.0

    # Perfect Storm: 3+ high-severity AND diverging trajectories
    if high_severity >= 3 and has_diverging:
        return "Perfect Storm"

    # Fog: insufficient data or dimension_collapse hiding quality
    if total_trajectories < 3 or len(scores) < 3:
        return "Fog"
    if has_dimension_collapse:
        return "Fog"

    # Stormy: multiple high-severity anomalies or diverging
    if high_severity >= 2 or (has_diverging and total_anomalies >= 2):
        return "Stormy"

    # Overcast: mostly non-contractive, plateau patterns, scores 70-85 range
    non_contractive_majority = contractive_count < total_trajectories / 2
    has_plateau = "plateau" in traj_types
    if non_contractive_majority or (has_plateau and 70 <= avg_score <= 85):
        return "Overcast"

    # Partly Cloudy: mixed contractive/non-contractive, minor anomalies
    if not all_contractive or (1 <= total_anomalies <= 2) or (60 <= avg_score < 80):
        return "Partly Cloudy"

    # Clear Skies: all contractive, no anomalies, avg score >80
    if all_contractive and total_anomalies == 0 and avg_score > 80:
        return "Clear Skies"

    # Default: Partly Cloudy for anything that doesn't fit
    return "Partly Cloudy"


def _compute_velocity(runs: list[dict]) -> float:
    """Compute average velocity (score gain per depth step) across runs."""
    velocities = []
    trajectories = _extract_trajectories(runs)
    for traj in trajectories:
        if len(traj) >= 2:
            velocity = (traj[-1] - traj[0]) / max(len(traj) - 1, 1)
            velocities.append(velocity)
    return sum(velocities) / len(velocities) if velocities else 0.0


def _generate_forecast(
    condition: str,
    trajectory_data: dict[str, Any],
    anomaly_data: dict[str, Any],
    scores: list[float],
    metrics_path: Optional[Path] = None,
) -> tuple[str, float]:
    """Generate a weather forecast using the predictor.

    Returns:
        (forecast_text, predictor_confidence) tuple.
    """
    # Try predictor for a quantitative signal
    predictor_prob = None
    predictor_confidence = 0.5
    if HAS_CONVERGE_PREDICTOR:
        try:
            result = predict_success(
                task="next convergence run",
                task_type="general",
                max_depth=3,
                metrics_path=metrics_path,
            )
            predictor_prob = result.probability
            predictor_confidence = result.confidence
        except Exception:
            pass

    # Build forecast text from condition + predictor
    forecasts = {
        "Clear Skies": "Continued clear conditions expected. Convergence runs are healthy and contractive.",
        "Partly Cloudy": "Some variability ahead. Recent runs show mixed signals -- monitor for emerging patterns.",
        "Overcast": "Stagnation risk. Scores are plateauing and trajectories lack contraction. Consider changing strategy.",
        "Stormy": "Rough conditions ahead. Multiple anomalies detected. Review recent runs before starting new convergence.",
        "Fog": "Low visibility -- insufficient data to assess conditions reliably. More runs needed.",
        "Perfect Storm": "Severe convergence disruption. Diverging trajectories with multiple high-severity anomalies. Halt and investigate.",
    }

    forecast = forecasts.get(condition, "Conditions unclear.")

    if predictor_prob is not None:
        if predictor_prob >= 0.8:
            forecast += f" Predictor gives {predictor_prob:.0%} success probability -- favorable outlook."
        elif predictor_prob >= 0.5:
            forecast += f" Predictor gives {predictor_prob:.0%} success probability -- proceed with caution."
        else:
            forecast += f" Predictor gives {predictor_prob:.0%} success probability -- consider postponing."

    return forecast, predictor_confidence


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_report(
    metrics_path: Optional[Path] = None,
    max_runs: int = 10,
) -> WeatherReport:
    """Generate a convergence weather report.

    Combines trajectory analysis, anomaly detection, and success prediction
    into a single metaphorical weather report.

    Args:
        metrics_path: Path to converge-metrics.jsonl. Defaults to standard location.
        max_runs: Maximum number of recent runs to analyze.

    Returns:
        WeatherReport with all fields populated.
    """
    path = metrics_path or DEFAULT_METRICS_PATH
    runs = _load_recent_runs(path, max_runs=max_runs)

    if not runs:
        return WeatherReport(
            condition="Fog",
            temperature=0.0,
            wind_speed=0.0,
            pressure=0.0,
            visibility=1.0,
            anomaly_count=0,
            forecast="No convergence data available. Run some converge loops to generate weather data.",
            confidence=0.0,
            details={"reason": "no_data"},
        )

    # Analyze
    scores = _extract_scores(runs)
    trajectory_data = _analyze_trajectories(runs)
    anomaly_data = _analyze_anomalies(runs, path)

    # Classify condition
    condition = _classify_condition(trajectory_data, anomaly_data, scores)

    # Compute metrics
    temperature = sum(scores) / len(scores) if scores else 0.0
    wind_speed = _compute_velocity(runs)
    pressure = trajectory_data.get("avg_ratio", 0.0)
    anomaly_rate = anomaly_data.get("anomaly_rate", 0.0)
    visibility = 1.0 - anomaly_rate
    anomaly_count = anomaly_data.get("total_anomalies", 0)

    # Generate forecast
    forecast, predictor_confidence = _generate_forecast(
        condition, trajectory_data, anomaly_data, scores, path,
    )

    # Overall confidence: weighted combination
    data_confidence = min(1.0, len(scores) / 5.0)  # more data = higher confidence
    module_confidence = (
        (0.4 if HAS_CONTRACTION_FITNESS else 0.0)
        + (0.3 if HAS_CONVERGE_ANOMALY else 0.0)
        + (0.3 if HAS_CONVERGE_PREDICTOR else 0.0)
    )
    confidence = round(data_confidence * module_confidence, 2)

    return WeatherReport(
        condition=condition,
        temperature=round(temperature, 1),
        wind_speed=round(wind_speed, 1),
        pressure=round(pressure, 4),
        visibility=round(visibility, 2),
        anomaly_count=anomaly_count,
        forecast=forecast,
        confidence=confidence,
        details={
            "runs_analyzed": len(runs),
            "scores": scores,
            "trajectory_data": {
                k: v for k, v in trajectory_data.items()
                if k != "trajectory_types"
            },
            "anomaly_data": {
                k: v for k, v in anomaly_data.items()
                if k != "anomaly_types"
            },
            "modules_available": {
                "contraction_fitness": HAS_CONTRACTION_FITNESS,
                "converge_anomaly": HAS_CONVERGE_ANOMALY,
                "converge_predictor": HAS_CONVERGE_PREDICTOR,
            },
        },
    )


def format_report(report: WeatherReport) -> str:
    """Format a WeatherReport as human-friendly ASCII output.

    Args:
        report: The WeatherReport to format.

    Returns:
        Multi-line string with weather dashboard.
    """
    # ASCII art per condition
    art = {
        "Clear Skies": [
            r"    \   /    ",
            r"     .-.     ",
            r"  - (   ) -  ",
            r"     `-'     ",
            r"    /   \    ",
        ],
        "Partly Cloudy": [
            r"   \  /      ",
            r" _ /''.--.   ",
            r"   \_(    ). ",
            r"   /(___(__) ",
            r"             ",
        ],
        "Overcast": [
            r"             ",
            r"     .--.    ",
            r"  .-(    ).  ",
            r" (___.__)__) ",
            r"             ",
        ],
        "Stormy": [
            r"     .-.     ",
            r"    (   ).   ",
            r"   (___(__)  ",
            r"  ///  ///   ",
            r"  ''  ''     ",
        ],
        "Fog": [
            r"             ",
            r" _ - _ - _ - ",
            r"  _ - _ - _  ",
            r" _ - _ - _ - ",
            r"             ",
        ],
        "Perfect Storm": [
            r"     .-.     ",
            r"    (   ).   ",
            r"   (___(__)  ",
            r"  *///\\\*   ",
            r" *''*  *''*  ",
        ],
    }

    condition_art = art.get(report.condition, art["Fog"])

    # Temperature bar (0-100 scale, 20 chars wide)
    temp_bar_len = max(0, min(20, int(report.temperature / 5)))
    temp_bar = "#" * temp_bar_len + "." * (20 - temp_bar_len)

    # Wind indicator
    if report.wind_speed <= 0:
        wind_desc = "Calm"
    elif report.wind_speed <= 5:
        wind_desc = "Light breeze"
    elif report.wind_speed <= 15:
        wind_desc = "Moderate"
    elif report.wind_speed <= 25:
        wind_desc = "Strong"
    else:
        wind_desc = "Gale force"

    # Pressure description
    if report.pressure <= 0.3:
        pressure_desc = "Very stable"
    elif report.pressure <= 0.6:
        pressure_desc = "Stable"
    elif report.pressure <= 0.9:
        pressure_desc = "Unstable"
    else:
        pressure_desc = "Highly unstable"

    lines = [
        "Convergence Weather Report",
        "=" * 42,
        "",
    ]

    # Art + condition side by side
    for i, art_line in enumerate(condition_art):
        if i == 2:
            lines.append(f"{art_line}  {report.condition}")
        else:
            lines.append(art_line)

    lines.extend([
        "",
        f"  Temperature:  {report.temperature:.1f}  [{temp_bar}]",
        f"  Wind Speed:   {report.wind_speed:.1f}  ({wind_desc})",
        f"  Pressure:     {report.pressure:.4f}  ({pressure_desc})",
        f"  Visibility:   {report.visibility:.0%}",
        f"  Anomalies:    {report.anomaly_count}",
        f"  Confidence:   {report.confidence:.0%}",
        "",
        "Forecast:",
        f"  {report.forecast}",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_cli() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="converge_weather",
        description="Convergence weather report -- metaphorical status dashboard",
    )
    sub = parser.add_subparsers(dest="command")

    forecast_p = sub.add_parser("forecast", help="Generate weather report")
    forecast_p.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output as JSON",
    )
    forecast_p.add_argument(
        "--max-runs", type=int, default=10,
        help="Maximum recent runs to analyze (default: 10)",
    )
    forecast_p.add_argument(
        "--metrics-path", type=str, default=None,
        help="Override path to converge-metrics.jsonl",
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = build_cli()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "forecast":
        path = Path(args.metrics_path) if args.metrics_path else None
        report = generate_report(metrics_path=path, max_runs=args.max_runs)

        if args.json_output:
            print(json.dumps(asdict(report), indent=2))
        else:
            print(format_report(report))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
