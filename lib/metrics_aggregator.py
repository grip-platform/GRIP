"""GRIP Metrics Aggregator -- time-series analytics across modules.

Aggregates run data, PR stats, and test counts into daily, weekly, and
monthly summaries with trend detection and multiple export formats.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Sequence


@dataclass
class DailyMetrics:
    """Metrics for a single calendar day."""

    date: date
    pr_count: int = 0
    test_count: int = 0
    loc_added: int = 0
    avg_score: float = 0.0
    runs_count: int = 0
    satisfied_count: int = 0
    avg_depth: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DailyMetrics":
        d = dict(d)
        if isinstance(d["date"], str):
            d["date"] = date.fromisoformat(d["date"])
        return cls(**d)


@dataclass
class PeriodSummary:
    """Aggregated summary for a time period (week or month)."""

    period: str
    start_date: date
    end_date: date
    metrics: DailyMetrics
    trend: str = "stable"  # improving / declining / stable

    def to_dict(self) -> dict:
        d = {
            "period": self.period,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "metrics": self.metrics.to_dict(),
            "trend": self.trend,
        }
        return d


# ---------------------------------------------------------------------------
# Core aggregation
# ---------------------------------------------------------------------------


def _parse_timestamp(ts: str) -> date:
    """Parse an ISO-8601 timestamp string to a date."""
    return datetime.fromisoformat(ts).date()


def aggregate_daily(
    runs: Sequence[dict],
    prs: Optional[Sequence[dict]] = None,
) -> List[DailyMetrics]:
    """Group run data (and optional PR data) by calendar date.

    Each run dict must have at least ``timestamp``.  Optional fields:
    ``satisfied`` (bool), ``depth_reached`` (int/float),
    ``final_dimension_scores`` (dict of float values), ``test_count`` (int),
    ``loc_added`` (int).

    PR dicts need ``merged_at`` or ``created_at`` (ISO timestamp).
    """
    buckets: dict[date, dict] = defaultdict(
        lambda: {
            "runs": 0,
            "satisfied": 0,
            "scores": [],
            "depths": [],
            "tests": 0,
            "loc": 0,
            "prs": 0,
        }
    )

    for run in runs:
        ts = run.get("timestamp")
        if not ts:
            continue
        d = _parse_timestamp(ts)
        b = buckets[d]
        b["runs"] += 1
        if run.get("satisfied"):
            b["satisfied"] += 1
        depth = run.get("depth_reached")
        if depth is not None:
            b["depths"].append(float(depth))
        scores = run.get("final_dimension_scores")
        if scores and isinstance(scores, dict):
            vals = [v for v in scores.values() if isinstance(v, (int, float))]
            if vals:
                b["scores"].append(sum(vals) / len(vals))
        b["tests"] += int(run.get("test_count", 0))
        b["loc"] += int(run.get("loc_added", 0))

    if prs:
        for pr in prs:
            ts = pr.get("merged_at") or pr.get("created_at")
            if not ts:
                continue
            d = _parse_timestamp(ts)
            buckets[d]["prs"] += 1

    result: List[DailyMetrics] = []
    for d in sorted(buckets):
        b = buckets[d]
        avg_score = sum(b["scores"]) / len(b["scores"]) if b["scores"] else 0.0
        avg_depth = sum(b["depths"]) / len(b["depths"]) if b["depths"] else 0.0
        result.append(
            DailyMetrics(
                date=d,
                pr_count=b["prs"],
                test_count=b["tests"],
                loc_added=b["loc"],
                avg_score=round(avg_score, 2),
                runs_count=b["runs"],
                satisfied_count=b["satisfied"],
                avg_depth=round(avg_depth, 2),
            )
        )
    return result


def _aggregate_period(
    daily: List[DailyMetrics],
    period_label: str,
    start: date,
    end: date,
) -> PeriodSummary:
    """Collapse a list of DailyMetrics into a single PeriodSummary."""
    if not daily:
        agg = DailyMetrics(date=start)
        return PeriodSummary(
            period=period_label, start_date=start, end_date=end,
            metrics=agg, trend="stable",
        )

    total_runs = sum(m.runs_count for m in daily)
    total_satisfied = sum(m.satisfied_count for m in daily)
    total_prs = sum(m.pr_count for m in daily)
    total_tests = sum(m.test_count for m in daily)
    total_loc = sum(m.loc_added for m in daily)

    scores = [m.avg_score for m in daily if m.avg_score > 0]
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0

    depths = [m.avg_depth for m in daily if m.avg_depth > 0]
    avg_depth = round(sum(depths) / len(depths), 2) if depths else 0.0

    agg = DailyMetrics(
        date=start,
        pr_count=total_prs,
        test_count=total_tests,
        loc_added=total_loc,
        avg_score=avg_score,
        runs_count=total_runs,
        satisfied_count=total_satisfied,
        avg_depth=avg_depth,
    )

    score_vals = [m.avg_score for m in daily]
    trend = compute_trend(score_vals)

    return PeriodSummary(
        period=period_label, start_date=start, end_date=end,
        metrics=agg, trend=trend,
    )


def _iso_week_start(d: date) -> date:
    """Return Monday of the ISO week containing *d*."""
    return d - timedelta(days=d.weekday())


def summarize_weekly(daily_metrics: List[DailyMetrics]) -> List[PeriodSummary]:
    """Aggregate daily metrics into ISO-week summaries."""
    if not daily_metrics:
        return []

    weeks: dict[date, List[DailyMetrics]] = defaultdict(list)
    for m in daily_metrics:
        week_start = _iso_week_start(m.date)
        weeks[week_start].append(m)

    result: List[PeriodSummary] = []
    for ws in sorted(weeks):
        we = ws + timedelta(days=6)
        label = f"W{ws.isocalendar()[1]:02d}-{ws.year}"
        result.append(_aggregate_period(weeks[ws], label, ws, we))
    return result


def summarize_monthly(daily_metrics: List[DailyMetrics]) -> List[PeriodSummary]:
    """Aggregate daily metrics into calendar-month summaries."""
    if not daily_metrics:
        return []

    months: dict[str, List[DailyMetrics]] = defaultdict(list)
    for m in daily_metrics:
        key = m.date.strftime("%Y-%m")
        months[key].append(m)

    result: List[PeriodSummary] = []
    for key in sorted(months):
        items = months[key]
        start = items[0].date.replace(day=1)
        # last day of month
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = start.replace(month=start.month + 1, day=1) - timedelta(days=1)
        label = start.strftime("%b %Y")
        result.append(_aggregate_period(items, label, start, end))
    return result


# ---------------------------------------------------------------------------
# Trend detection
# ---------------------------------------------------------------------------


def compute_trend(values: Sequence[float], window: int = 3) -> str:
    """Detect trend direction using a moving-average comparison.

    Returns ``"improving"``, ``"declining"``, or ``"stable"``.
    """
    if len(values) < window:
        return "stable"

    # Compare the average of the last *window* values to the first *window*
    first_avg = sum(values[:window]) / window
    last_avg = sum(values[-window:]) / window

    if first_avg == 0 and last_avg == 0:
        return "stable"

    # Use relative change when possible, absolute otherwise
    if first_avg != 0:
        change = (last_avg - first_avg) / abs(first_avg)
    else:
        change = last_avg  # first was 0, any positive is improving

    threshold = 0.05  # 5 % change required to be non-stable
    if change > threshold:
        return "improving"
    elif change < -threshold:
        return "declining"
    return "stable"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_DAILY_COLUMNS = [
    ("Date", 12),
    ("Runs", 6),
    ("OK", 4),
    ("PRs", 5),
    ("Tests", 6),
    ("LOC+", 6),
    ("Score", 7),
    ("Depth", 7),
]


def format_daily_report(metrics: List[DailyMetrics]) -> str:
    """Render an ASCII table of daily metrics."""
    if not metrics:
        return "(no data)"

    header = "  ".join(name.ljust(w) for name, w in _DAILY_COLUMNS)
    sep = "  ".join("-" * w for _, w in _DAILY_COLUMNS)
    lines = [header, sep]

    for m in metrics:
        row = [
            str(m.date).ljust(12),
            str(m.runs_count).rjust(6),
            str(m.satisfied_count).rjust(4),
            str(m.pr_count).rjust(5),
            str(m.test_count).rjust(6),
            str(m.loc_added).rjust(6),
            f"{m.avg_score:.2f}".rjust(7),
            f"{m.avg_depth:.1f}".rjust(7),
        ]
        lines.append("  ".join(row))
    return "\n".join(lines)


def format_summary(summary: PeriodSummary) -> str:
    """Human-readable period summary."""
    m = summary.metrics
    lines = [
        f"Period:    {summary.period} ({summary.start_date} to {summary.end_date})",
        f"Trend:     {summary.trend}",
        f"Runs:      {m.runs_count}  (satisfied: {m.satisfied_count})",
        f"PRs:       {m.pr_count}",
        f"Tests:     {m.test_count}",
        f"LOC added: {m.loc_added}",
        f"Avg score: {m.avg_score:.2f}",
        f"Avg depth: {m.avg_depth:.1f}",
    ]
    return "\n".join(lines)


def format_trend_chart(
    values: Sequence[float],
    label: str = "value",
    width: int = 40,
) -> str:
    """Render an ASCII bar chart / sparkline for a sequence of values."""
    if not values:
        return f"{label}: (no data)"

    max_val = max(values) if max(values) > 0 else 1.0
    lines = [f"{label} trend ({len(values)} points, max={max_val:.2f})"]
    lines.append("-" * (width + 20))

    for i, v in enumerate(values):
        bar_len = int((v / max_val) * width) if max_val else 0
        bar = "#" * bar_len
        lines.append(f"  {i:>3d} | {bar:<{width}} | {v:.2f}")

    trend_dir = compute_trend(list(values))
    lines.append(f"Trend: {trend_dir}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def export_csv(metrics: List[DailyMetrics], filepath: str) -> str:
    """Export daily metrics to a CSV file. Returns the absolute path written."""
    path = Path(filepath).resolve()
    fieldnames = [
        "date", "runs_count", "satisfied_count", "pr_count",
        "test_count", "loc_added", "avg_score", "avg_depth",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for m in metrics:
            row = m.to_dict()
            # Keep only the columns we want, in order
            writer.writerow({k: row[k] for k in fieldnames})
    return str(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_runs(path: str) -> List[dict]:
    """Load runs from a JSON or JSONL file."""
    p = Path(path)
    text = p.read_text()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        return [data]
    except json.JSONDecodeError:
        # Try JSONL
        runs = []
        for line in text.strip().splitlines():
            line = line.strip()
            if line:
                runs.append(json.loads(line))
        return runs


def _extract_metric(daily: List[DailyMetrics], metric: str) -> List[float]:
    """Extract a named metric series from daily data."""
    mapping = {
        "score": "avg_score",
        "depth": "avg_depth",
        "runs": "runs_count",
        "tests": "test_count",
    }
    attr = mapping.get(metric, metric)
    return [float(getattr(m, attr, 0)) for m in daily]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="metrics_aggregator",
        description="GRIP Metrics Aggregator",
    )
    parser.add_argument(
        "command",
        choices=["daily", "weekly", "monthly", "trend", "export"],
        help="Report type",
    )
    parser.add_argument(
        "args",
        nargs="*",
        help="Subcommand arguments (metric name for trend, 'csv <path>' for export)",
    )
    parser.add_argument(
        "--runs", "-r",
        default=None,
        help="Path to runs JSON/JSONL file",
    )
    parser.add_argument(
        "--prs",
        default=None,
        help="Path to PRs JSON file",
    )

    opts = parser.parse_args(argv)

    runs = _load_runs(opts.runs) if opts.runs else []
    prs_data = json.loads(Path(opts.prs).read_text()) if opts.prs else None
    if prs_data and not isinstance(prs_data, list):
        prs_data = [prs_data]

    daily = aggregate_daily(runs, prs=prs_data)

    if opts.command == "daily":
        print(format_daily_report(daily))
    elif opts.command == "weekly":
        for s in summarize_weekly(daily):
            print(format_summary(s))
            print()
    elif opts.command == "monthly":
        for s in summarize_monthly(daily):
            print(format_summary(s))
            print()
    elif opts.command == "trend":
        metric = opts.args[0] if opts.args else "score"
        values = _extract_metric(daily, metric)
        print(format_trend_chart(values, label=metric))
    elif opts.command == "export":
        if len(opts.args) < 2 or opts.args[0] != "csv":
            print("Usage: export csv <filepath>", file=sys.stderr)
            return 1
        out = export_csv(daily, opts.args[1])
        print(f"Exported to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
