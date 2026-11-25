#!/usr/bin/env python3
"""Sprint velocity and acceleration across waves.

Tracks PR throughput, test output, and LOC velocity per wave,
computes acceleration (rate of change), detects trends, and
identifies optimal wave sizes for maximum throughput.

Wave data format::

    {"wave": 10,
     "prs": [{"number": 562, "tests": 91, "loc": 1048}],
     "start_time": "2026-01-01T00:00:00",
     "end_time": "2026-01-01T02:30:00"}

CLI subcommands: velocity | history | acceleration | optimal-size
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WaveMetrics:
    """Velocity metrics for a single wave."""

    wave_number: int
    prs_merged: int
    tests_written: int
    loc_added: int
    duration_minutes: float
    prs_per_hour: float = 0.0
    tests_per_hour: float = 0.0
    loc_per_hour: float = 0.0

    def __post_init__(self) -> None:
        if self.duration_minutes > 0:
            hours = self.duration_minutes / 60.0
            if self.prs_per_hour == 0.0:
                self.prs_per_hour = self.prs_merged / hours
            if self.tests_per_hour == 0.0:
                self.tests_per_hour = self.tests_written / hours
            if self.loc_per_hour == 0.0:
                self.loc_per_hour = self.loc_added / hours


@dataclass
class VelocityReport:
    """Aggregate velocity report across waves."""

    waves: list[WaveMetrics] = field(default_factory=list)
    acceleration: float = 0.0
    trend: str = "steady"
    optimal_wave_size: int = 0


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

def _parse_iso(ts: str) -> datetime:
    """Parse ISO 8601 timestamp, tolerant of trailing Z."""
    ts = ts.rstrip("Z")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised timestamp format: {ts}")


# ---------------------------------------------------------------------------
# Core computations
# ---------------------------------------------------------------------------

def compute_wave_metrics(wave_data: dict) -> WaveMetrics:
    """Calculate velocity metrics for a single wave.

    Args:
        wave_data: Dict with keys ``wave``, ``prs`` (list of dicts with
                   ``number``/``tests``/``loc``), ``start_time``, ``end_time``.

    Returns:
        WaveMetrics with computed per-hour rates.

    Raises:
        ValueError: If end_time precedes start_time.
    """
    wave_number = wave_data["wave"]
    prs = wave_data.get("prs", [])
    prs_merged = len(prs)
    tests_written = sum(pr.get("tests", 0) for pr in prs)
    loc_added = sum(pr.get("loc", 0) for pr in prs)

    start = _parse_iso(wave_data["start_time"])
    end = _parse_iso(wave_data["end_time"])
    duration_minutes = (end - start).total_seconds() / 60.0

    if duration_minutes < 0:
        raise ValueError(
            f"Wave {wave_number}: end_time is before start_time"
        )

    return WaveMetrics(
        wave_number=wave_number,
        prs_merged=prs_merged,
        tests_written=tests_written,
        loc_added=loc_added,
        duration_minutes=duration_minutes,
    )


def compute_acceleration(waves: list[WaveMetrics]) -> float:
    """Rate of change of velocity (PRs/hour) across waves.

    Uses linear regression slope of ``prs_per_hour`` over wave index.
    Returns ``0.0`` for fewer than 2 waves.
    """
    if len(waves) < 2:
        return 0.0

    n = len(waves)
    xs = list(range(n))
    ys = [w.prs_per_hour for w in waves]

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = sum((x - mean_x) ** 2 for x in xs)

    if denominator == 0:
        return 0.0

    return numerator / denominator


def detect_trend(
    waves: list[WaveMetrics], threshold: float = 0.05
) -> str:
    """Classify velocity trend as accelerating / decelerating / steady.

    Args:
        waves: Ordered list of wave metrics.
        threshold: Minimum absolute acceleration to classify as non-steady.

    Returns:
        One of ``"accelerating"``, ``"decelerating"``, ``"steady"``.
    """
    acc = compute_acceleration(waves)
    if acc > threshold:
        return "accelerating"
    elif acc < -threshold:
        return "decelerating"
    return "steady"


def find_optimal_wave_size(waves: list[WaveMetrics]) -> int:
    """Which wave size (number of PRs) produces best throughput.

    Groups waves by ``prs_merged`` count and returns the PR count
    with the highest average ``prs_per_hour``.  Returns ``0`` if no waves.
    """
    if not waves:
        return 0

    groups: dict[int, list[float]] = {}
    for w in waves:
        groups.setdefault(w.prs_merged, []).append(w.prs_per_hour)

    best_size = 0
    best_avg = -1.0
    for size, rates in sorted(groups.items()):
        avg = sum(rates) / len(rates)
        if avg > best_avg:
            best_avg = avg
            best_size = size

    return best_size


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_velocity_chart(waves: list[WaveMetrics], width: int = 40) -> str:
    """ASCII bar chart of ``prs_per_hour`` over waves.

    Args:
        waves: Ordered wave metrics.
        width: Maximum bar width in characters.

    Returns:
        Multi-line ASCII chart string.
    """
    if not waves:
        return "No wave data."

    max_rate = max(w.prs_per_hour for w in waves)
    if max_rate == 0:
        max_rate = 1.0  # avoid division by zero

    lines: list[str] = []
    lines.append("Velocity (PRs/hour)")
    lines.append("")

    for w in waves:
        bar_len = int((w.prs_per_hour / max_rate) * width)
        bar = "#" * bar_len
        lines.append(f"  Wave {w.wave_number:>3} | {bar} {w.prs_per_hour:.2f}")

    lines.append("")
    return "\n".join(lines)


def format_report(report: VelocityReport) -> str:
    """Human-readable velocity report."""
    lines: list[str] = []
    lines.append("Sprint Velocity Report")
    lines.append("=" * 50)
    lines.append("")

    if not report.waves:
        lines.append("No wave data available.")
        return "\n".join(lines)

    # Summary
    total_prs = sum(w.prs_merged for w in report.waves)
    total_tests = sum(w.tests_written for w in report.waves)
    total_loc = sum(w.loc_added for w in report.waves)
    total_minutes = sum(w.duration_minutes for w in report.waves)

    lines.append(f"Waves analysed:     {len(report.waves)}")
    lines.append(f"Total PRs:          {total_prs}")
    lines.append(f"Total tests:        {total_tests}")
    lines.append(f"Total LOC:          {total_loc}")
    if total_minutes > 0:
        total_hours = total_minutes / 60.0
        lines.append(f"Total time:         {total_hours:.1f} hours")
        lines.append(f"Overall PRs/hour:   {total_prs / total_hours:.2f}")
    lines.append("")

    # Per-wave table
    lines.append(
        f"{'Wave':>6} {'PRs':>5} {'Tests':>6} {'LOC':>7} "
        f"{'PRs/h':>7} {'Tests/h':>8} {'LOC/h':>8}"
    )
    lines.append("-" * 55)
    for w in report.waves:
        lines.append(
            f"{w.wave_number:>6} {w.prs_merged:>5} {w.tests_written:>6} "
            f"{w.loc_added:>7} {w.prs_per_hour:>7.2f} {w.tests_per_hour:>8.1f} "
            f"{w.loc_per_hour:>8.0f}"
        )
    lines.append("")

    # Trend
    lines.append(f"Acceleration:       {report.acceleration:+.4f} PRs/hour per wave")
    lines.append(f"Trend:              {report.trend}")
    if report.optimal_wave_size > 0:
        lines.append(f"Optimal wave size:  {report.optimal_wave_size} PRs")
    lines.append("")

    # Chart
    lines.append(format_velocity_chart(report.waves))

    return "\n".join(lines)


def compare_waves(wave_a: WaveMetrics, wave_b: WaveMetrics) -> str:
    """Side-by-side comparison of two waves with deltas."""
    lines: list[str] = []
    header = (
        f"{'Metric':<20} "
        f"{'Wave ' + str(wave_a.wave_number):>12} "
        f"{'Wave ' + str(wave_b.wave_number):>12} "
        f"{'Delta':>12}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    comparisons = [
        ("PRs merged", wave_a.prs_merged, wave_b.prs_merged, "d"),
        ("Tests written", wave_a.tests_written, wave_b.tests_written, "d"),
        ("LOC added", wave_a.loc_added, wave_b.loc_added, "d"),
        ("Duration (min)", wave_a.duration_minutes, wave_b.duration_minutes, ".1f"),
        ("PRs/hour", wave_a.prs_per_hour, wave_b.prs_per_hour, ".2f"),
        ("Tests/hour", wave_a.tests_per_hour, wave_b.tests_per_hour, ".1f"),
        ("LOC/hour", wave_a.loc_per_hour, wave_b.loc_per_hour, ".0f"),
    ]

    for label, a_val, b_val, fmt in comparisons:
        delta = b_val - a_val
        sign = "+" if delta > 0 else ""
        a_str = f"{a_val:{fmt}}"
        b_str = f"{b_val:{fmt}}"
        d_str = f"{sign}{delta:{fmt}}"
        lines.append(f"{label:<20} {a_str:>12} {b_str:>12} {d_str:>12}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def build_report(wave_data_list: list[dict]) -> VelocityReport:
    """Build a full velocity report from raw wave data.

    Args:
        wave_data_list: List of wave dicts (see module docstring for format).

    Returns:
        VelocityReport with all fields computed.
    """
    waves = [compute_wave_metrics(wd) for wd in wave_data_list]
    waves.sort(key=lambda w: w.wave_number)

    return VelocityReport(
        waves=waves,
        acceleration=compute_acceleration(waves),
        trend=detect_trend(waves),
        optimal_wave_size=find_optimal_wave_size(waves),
    )


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------

def _cmd_velocity(args: argparse.Namespace) -> None:
    """Show current (latest) wave velocity."""
    data = json.load(args.input)
    if not isinstance(data, list):
        data = [data]
    report = build_report(data)
    if report.waves:
        latest = report.waves[-1]
        print(
            f"Wave {latest.wave_number}: "
            f"{latest.prs_per_hour:.2f} PRs/hour, "
            f"{latest.tests_per_hour:.1f} tests/hour, "
            f"{latest.loc_per_hour:.0f} LOC/hour"
        )
    else:
        print("No wave data.")


def _cmd_history(args: argparse.Namespace) -> None:
    """Show full velocity history."""
    data = json.load(args.input)
    if not isinstance(data, list):
        data = [data]
    report = build_report(data)
    print(format_report(report))


def _cmd_acceleration(args: argparse.Namespace) -> None:
    """Show acceleration metric."""
    data = json.load(args.input)
    if not isinstance(data, list):
        data = [data]
    report = build_report(data)
    print(f"Acceleration: {report.acceleration:+.4f} PRs/hour per wave")
    print(f"Trend: {report.trend}")


def _cmd_optimal_size(args: argparse.Namespace) -> None:
    """Show optimal wave size."""
    data = json.load(args.input)
    if not isinstance(data, list):
        data = [data]
    report = build_report(data)
    if report.optimal_wave_size > 0:
        print(f"Optimal wave size: {report.optimal_wave_size} PRs")
    else:
        print("Insufficient data to determine optimal wave size.")


def main(argv: Optional[list[str]] = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Sprint velocity tracker -- measure acceleration across waves",
    )
    parser.add_argument(
        "--input", "-i",
        type=argparse.FileType("r"),
        default=sys.stdin,
        help="JSON file with wave data (default: stdin)",
    )

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("velocity", help="Current wave velocity")
    sub.add_parser("history", help="Full velocity history")
    sub.add_parser("acceleration", help="Acceleration metric")
    sub.add_parser("optimal-size", help="Optimal wave size")

    args = parser.parse_args(argv)

    commands = {
        "velocity": _cmd_velocity,
        "history": _cmd_history,
        "acceleration": _cmd_acceleration,
        "optimal-size": _cmd_optimal_size,
    }

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()
