#!/usr/bin/env python3
"""
confidence_calibrator.py -- Measures prediction accuracy of confidence estimates.

Calibration is the alignment between predicted confidence and actual outcomes.
A well-calibrated system predicting 80% confidence should succeed ~80% of the
time. This module quantifies that alignment via calibration curves, Brier scores,
and over/under-confidence detection.

Feeds into GRIP's converge_metrics pipeline: after each converge run, the
predicted confidence ("I'm 85% sure this satisfies the criterion") is compared
to the actual outcome. Over time, build_calibration_curve() reveals systematic
biases -- typically overconfidence in the 0.8-1.0 range.

Usage:
    from lib.confidence_calibrator import (
        build_records, build_calibration_curve, compute_brier_score,
        detect_overconfidence, detect_underconfidence,
        format_curve, format_accuracy_report,
    )

    records = build_records(forecasts, actuals)
    curve = build_calibration_curve(records)
    print(format_curve(curve))
    print(format_accuracy_report(curve))

CLI:
    python3 lib/confidence_calibrator.py calibrate [--file PATH]
    python3 lib/confidence_calibrator.py accuracy [--file PATH]
    python3 lib/confidence_calibrator.py overconfident [--file PATH]
    python3 lib/confidence_calibrator.py plot [--file PATH]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# Import paths
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DEFAULT_METRICS_PATH = CLAUDE_DIR / "learning" / "converge-metrics.jsonl"


# =========================================================================
# Data Structures
# =========================================================================


@dataclass
class PredictionRecord:
    """A single prediction matched to its outcome."""

    run_id: str
    predicted_confidence: float  # 0.0 - 1.0
    predicted_satisfied: bool
    actual_satisfied: bool

    def __post_init__(self) -> None:
        if not 0.0 <= self.predicted_confidence <= 1.0:
            raise ValueError(
                f"predicted_confidence must be in [0.0, 1.0], got {self.predicted_confidence}"
            )


@dataclass
class CalibrationPoint:
    """One bucket in the calibration curve."""

    confidence_bucket: float  # midpoint: 0.05, 0.15, ..., 0.95
    predicted_rate: float  # mean predicted confidence in bucket
    actual_rate: float  # fraction actually satisfied
    sample_count: int


@dataclass
class CalibrationCurve:
    """Full calibration analysis."""

    points: List[CalibrationPoint]
    overall_accuracy: float  # fraction of correct binary predictions
    brier_score: float  # mean squared error (lower = better)
    overconfidence_ratio: float  # fraction of buckets where predicted > actual
    underconfidence_ratio: float  # fraction of buckets where predicted < actual


# =========================================================================
# Record Building
# =========================================================================


def build_records(
    forecasts: Sequence[dict],
    actuals: Sequence[dict],
) -> List[PredictionRecord]:
    """Match prediction forecasts to actual outcomes by run_id.

    Args:
        forecasts: dicts with keys {run_id, predicted_confidence, predicted_satisfied}
        actuals: dicts with keys {run_id, actual_satisfied}

    Returns:
        List of PredictionRecord for matched run_ids.
    """
    actual_map = {a["run_id"]: a["actual_satisfied"] for a in actuals}
    records = []
    for f in forecasts:
        rid = f["run_id"]
        if rid in actual_map:
            records.append(
                PredictionRecord(
                    run_id=rid,
                    predicted_confidence=float(f["predicted_confidence"]),
                    predicted_satisfied=bool(f["predicted_satisfied"]),
                    actual_satisfied=bool(actual_map[rid]),
                )
            )
    return records


# =========================================================================
# Calibration Curve
# =========================================================================


def _bucket_index(confidence: float, buckets: int) -> int:
    """Map confidence [0.0, 1.0] to bucket index [0, buckets-1]."""
    idx = int(confidence * buckets)
    return min(idx, buckets - 1)


def _bucket_midpoint(index: int, buckets: int) -> float:
    """Return midpoint of the bucket."""
    width = 1.0 / buckets
    return round(index * width + width / 2, 4)


def build_calibration_curve(
    records: List[PredictionRecord],
    buckets: int = 10,
) -> CalibrationCurve:
    """Group records by confidence bucket and compute calibration curve.

    Args:
        records: prediction records to analyze.
        buckets: number of confidence buckets (default 10 for 0.1 steps).

    Returns:
        CalibrationCurve with per-bucket stats and aggregate metrics.

    Raises:
        ValueError: if records is empty or buckets < 1.
    """
    if not records:
        raise ValueError("Cannot build calibration curve from empty records")
    if buckets < 1:
        raise ValueError(f"buckets must be >= 1, got {buckets}")

    # Group by bucket
    bucket_confs: dict[int, list[float]] = {i: [] for i in range(buckets)}
    bucket_outcomes: dict[int, list[bool]] = {i: [] for i in range(buckets)}

    for r in records:
        idx = _bucket_index(r.predicted_confidence, buckets)
        bucket_confs[idx].append(r.predicted_confidence)
        bucket_outcomes[idx].append(r.actual_satisfied)

    points: List[CalibrationPoint] = []
    for i in range(buckets):
        confs = bucket_confs[i]
        outcomes = bucket_outcomes[i]
        if not confs:
            continue
        predicted_rate = sum(confs) / len(confs)
        actual_rate = sum(1 for o in outcomes if o) / len(outcomes)
        points.append(
            CalibrationPoint(
                confidence_bucket=_bucket_midpoint(i, buckets),
                predicted_rate=round(predicted_rate, 4),
                actual_rate=round(actual_rate, 4),
                sample_count=len(confs),
            )
        )

    brier = compute_brier_score(records)
    accuracy = _compute_accuracy(records)
    over_ratio, under_ratio = _compute_confidence_ratios(points)

    return CalibrationCurve(
        points=points,
        overall_accuracy=round(accuracy, 4),
        brier_score=round(brier, 6),
        overconfidence_ratio=round(over_ratio, 4),
        underconfidence_ratio=round(under_ratio, 4),
    )


def _compute_accuracy(records: List[PredictionRecord]) -> float:
    """Fraction of correct binary predictions."""
    if not records:
        return 0.0
    correct = sum(1 for r in records if r.predicted_satisfied == r.actual_satisfied)
    return correct / len(records)


def _compute_confidence_ratios(
    points: List[CalibrationPoint],
) -> Tuple[float, float]:
    """Compute fraction of buckets that are over/under-confident."""
    if not points:
        return 0.0, 0.0
    over = sum(1 for p in points if p.predicted_rate > p.actual_rate)
    under = sum(1 for p in points if p.predicted_rate < p.actual_rate)
    total = len(points)
    return over / total, under / total


# =========================================================================
# Brier Score
# =========================================================================


def compute_brier_score(records: List[PredictionRecord]) -> float:
    """Mean squared error between predicted confidence and binary outcome.

    Brier score ranges from 0 (perfect) to 1 (worst).
    A trivial baseline predicting 0.5 always scores 0.25.
    """
    if not records:
        return 0.0
    total = sum(
        (r.predicted_confidence - (1.0 if r.actual_satisfied else 0.0)) ** 2
        for r in records
    )
    return total / len(records)


# =========================================================================
# Over/Under-Confidence Detection
# =========================================================================


def detect_overconfidence(curve: CalibrationCurve) -> List[CalibrationPoint]:
    """Return buckets where predicted confidence exceeds actual success rate."""
    return [p for p in curve.points if p.predicted_rate > p.actual_rate]


def detect_underconfidence(curve: CalibrationCurve) -> List[CalibrationPoint]:
    """Return buckets where predicted confidence is below actual success rate."""
    return [p for p in curve.points if p.predicted_rate < p.actual_rate]


# =========================================================================
# ASCII Calibration Plot
# =========================================================================

PLOT_HEIGHT = 11
PLOT_WIDTH = 21


def format_curve(curve: CalibrationCurve) -> str:
    """Render ASCII calibration plot.

    The ideal calibration is the diagonal (predicted == actual).
    Actual data points are shown as '*'.
    """
    lines: List[str] = []
    lines.append("Calibration Plot (/ = ideal, * = actual)")
    lines.append("")

    # Build grid
    grid: List[List[str]] = [[" " for _ in range(PLOT_WIDTH)] for _ in range(PLOT_HEIGHT)]

    # Draw ideal diagonal
    for row in range(PLOT_HEIGHT):
        y = 1.0 - row / (PLOT_HEIGHT - 1)
        col = int(y * (PLOT_WIDTH - 1))
        if 0 <= col < PLOT_WIDTH:
            grid[row][col] = "/"

    # Plot actual points
    for p in curve.points:
        col = int(p.predicted_rate * (PLOT_WIDTH - 1))
        row = int((1.0 - p.actual_rate) * (PLOT_HEIGHT - 1))
        col = max(0, min(PLOT_WIDTH - 1, col))
        row = max(0, min(PLOT_HEIGHT - 1, row))
        grid[row][col] = "*"

    # Render with Y-axis labels
    for row in range(PLOT_HEIGHT):
        y_val = 1.0 - row / (PLOT_HEIGHT - 1)
        if row % 2 == 0:
            label = f"{y_val:.1f} "
        else:
            label = "    "
        bar = "|" if row < PLOT_HEIGHT - 1 else "+"
        line_content = "".join(grid[row])
        lines.append(f"{label}{bar}{line_content}")

    # X-axis
    lines.append("    " + "-" * (PLOT_WIDTH + 1))
    lines.append("    0.0  0.2  0.4  0.6  0.8  1.0")
    lines.append("         Predicted Confidence ->")

    return "\n".join(lines)


# =========================================================================
# Accuracy Report
# =========================================================================


def format_accuracy_report(curve: CalibrationCurve) -> str:
    """Human-readable accuracy summary."""
    lines: List[str] = []
    lines.append("Confidence Calibration Report")
    lines.append("=" * 40)
    lines.append("")
    lines.append(f"Overall Accuracy:      {curve.overall_accuracy:.1%}")
    lines.append(f"Brier Score:           {curve.brier_score:.4f}")
    lines.append(f"Overconfidence Ratio:  {curve.overconfidence_ratio:.1%}")
    lines.append(f"Underconfidence Ratio: {curve.underconfidence_ratio:.1%}")
    lines.append("")

    # Interpretation
    if curve.brier_score < 0.1:
        quality = "Excellent"
    elif curve.brier_score < 0.2:
        quality = "Good"
    elif curve.brier_score < 0.25:
        quality = "Fair (baseline-level)"
    else:
        quality = "Poor"
    lines.append(f"Calibration Quality:   {quality}")
    lines.append("")

    # Per-bucket table
    lines.append("Bucket  | Predicted | Actual  | Samples | Delta")
    lines.append("--------+-----------+---------+---------+------")
    for p in curve.points:
        delta = p.predicted_rate - p.actual_rate
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"  {p.confidence_bucket:.2f}  |   {p.predicted_rate:.2f}    |  {p.actual_rate:.2f}  |"
            f"   {p.sample_count:4d}  | {sign}{delta:.2f}"
        )

    # Over/underconfidence details
    over = detect_overconfidence(curve)
    under = detect_underconfidence(curve)
    if over:
        lines.append("")
        lines.append(
            f"Overconfident buckets ({len(over)}): "
            + ", ".join(f"{p.confidence_bucket:.2f}" for p in over)
        )
    if under:
        lines.append(
            f"Underconfident buckets ({len(under)}): "
            + ", ".join(f"{p.confidence_bucket:.2f}" for p in under)
        )

    return "\n".join(lines)


# =========================================================================
# JSONL Loading (from converge-metrics)
# =========================================================================


def load_records_from_metrics(
    path: Optional[Path] = None,
) -> List[PredictionRecord]:
    """Load PredictionRecords from converge-metrics JSONL.

    Expects events with type=converge_end containing:
      - run_id
      - predicted_confidence (or final_score/100 as fallback)
      - satisfied (bool)
    """
    metrics_path = path or DEFAULT_METRICS_PATH
    if not metrics_path.exists():
        return []

    events: List[dict] = []
    with open(metrics_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Extract converge_end events as both forecast and actual
    records: List[PredictionRecord] = []
    for ev in events:
        if ev.get("type") != "converge_end":
            continue
        run_id = ev.get("run_id", "")
        if not run_id:
            continue

        # Confidence: explicit or from final_score
        confidence = ev.get("predicted_confidence")
        if confidence is None:
            score = ev.get("final_score")
            if score is not None:
                confidence = float(score) / 100.0
            else:
                continue

        confidence = max(0.0, min(1.0, float(confidence)))
        satisfied = bool(ev.get("satisfied", False))

        # For converge_end, the predicted_satisfied is whether confidence >= 0.5
        records.append(
            PredictionRecord(
                run_id=run_id,
                predicted_confidence=confidence,
                predicted_satisfied=confidence >= 0.5,
                actual_satisfied=satisfied,
            )
        )

    return records


# =========================================================================
# CLI
# =========================================================================


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Confidence calibration analysis for GRIP converge loops."
    )
    parser.add_argument(
        "command",
        choices=["calibrate", "accuracy", "overconfident", "plot"],
        help="Command to run.",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Path to converge-metrics JSONL (default: learning/converge-metrics.jsonl).",
    )
    parser.add_argument(
        "--buckets",
        type=int,
        default=10,
        help="Number of calibration buckets (default: 10).",
    )

    args = parser.parse_args()
    records = load_records_from_metrics(args.file)

    if not records:
        print("No calibration data found. Run some /converge loops first.")
        sys.exit(0)

    curve = build_calibration_curve(records, buckets=args.buckets)

    if args.command == "calibrate":
        print(format_accuracy_report(curve))
        print()
        print(format_curve(curve))
    elif args.command == "accuracy":
        print(format_accuracy_report(curve))
    elif args.command == "overconfident":
        over = detect_overconfidence(curve)
        if not over:
            print("No overconfident buckets detected.")
        else:
            print(f"Overconfident buckets ({len(over)}):")
            for p in over:
                delta = p.predicted_rate - p.actual_rate
                print(
                    f"  Bucket {p.confidence_bucket:.2f}: "
                    f"predicted {p.predicted_rate:.2f}, "
                    f"actual {p.actual_rate:.2f}, "
                    f"delta +{delta:.2f} "
                    f"(n={p.sample_count})"
                )
    elif args.command == "plot":
        print(format_curve(curve))


if __name__ == "__main__":
    _cli()
