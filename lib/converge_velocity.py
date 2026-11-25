#!/usr/bin/env python3
"""
converge_velocity.py -- Convergence velocity tracking and trend analysis.

Tracks how fast and reliably GRIP's convergence loops reach their criteria.
Records each run to a JSONL log and provides rolling statistics, trend
detection, and dashboard formatting.

Usage:
    from lib.converge_velocity import VelocityTracker

    tracker = VelocityTracker()
    tracker.record("fix auth bug", iterations=3, time_s=42.5, depth=2, converged=True)

    print(tracker.format_dashboard())   # Full ASCII dashboard
    print(tracker.format_compact())     # Single statusline: conv:85% 3.2it 45s ^

    # CLI
    python3 lib/converge_velocity.py dashboard
    python3 lib/converge_velocity.py compact
    python3 lib/converge_velocity.py record --task "fix auth" --iterations 3 --time 42.5 --depth 2 --converged
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DEFAULT_DATA_DIR = CLAUDE_DIR / "data"
DEFAULT_LOG_FILE = "converge-velocity.jsonl"

# Sparkline characters (ascending blocks)
SPARK_CHARS = " " + "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


@dataclass
class VelocityRecord:
    """A single convergence run measurement."""

    task_hash: str
    iterations: int
    time_seconds: float
    depth: int
    converged: bool
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> VelocityRecord:
        return cls(
            task_hash=d["task_hash"],
            iterations=int(d["iterations"]),
            time_seconds=float(d["time_seconds"]),
            depth=int(d["depth"]),
            converged=bool(d["converged"]),
            timestamp=d["timestamp"],
        )


def _task_hash(task: str) -> str:
    """SHA-256 of task string, truncated to 8 hex chars."""
    return hashlib.sha256(task.encode("utf-8")).hexdigest()[:8]


def _sparkline(values: list[float], width: int = 20) -> str:
    """Render a sparkline string from a list of values.

    Args:
        values: Numeric values to chart.
        width: Maximum number of characters in the sparkline.

    Returns:
        A string of Unicode block characters representing the values.
    """
    if not values:
        return ""

    # Sample values to fit within width
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = list(values)

    lo = min(sampled)
    hi = max(sampled)
    span = hi - lo if hi != lo else 1.0

    chars = []
    for v in sampled:
        # Map to index 1..8 (skip the space at index 0)
        idx = int(((v - lo) / span) * 7) + 1
        idx = max(1, min(8, idx))
        chars.append(SPARK_CHARS[idx])

    return "".join(chars)


def _bar(label: str, count: int, max_count: int, width: int = 20) -> str:
    """Render a horizontal bar chart row."""
    if max_count == 0:
        bar_len = 0
    else:
        bar_len = int((count / max_count) * width)
    bar = "\u2588" * bar_len
    return f"  {label:>7s} | {bar} {count}"


class VelocityTracker:
    """Track and analyze convergence velocity over time."""

    def __init__(self, data_dir: Optional[Path] = None):
        self._data_dir = data_dir or DEFAULT_DATA_DIR
        self._log_path = self._data_dir / DEFAULT_LOG_FILE
        self._history: list[VelocityRecord] = []
        self._loaded = False

    @property
    def history(self) -> list[VelocityRecord]:
        """Lazy-load history on first access."""
        if not self._loaded:
            self._history = self.load_history()
            self._loaded = True
        return self._history

    def record(
        self,
        task: str,
        iterations: int,
        time_s: float,
        depth: int,
        converged: bool,
    ) -> VelocityRecord:
        """Append a convergence run to the JSONL log.

        Args:
            task: Description of the convergence task.
            iterations: Number of iterations taken.
            time_s: Wall-clock time in seconds.
            depth: Maximum convergence depth reached.
            converged: Whether the criterion was satisfied.

        Returns:
            The created VelocityRecord.
        """
        rec = VelocityRecord(
            task_hash=_task_hash(task),
            iterations=iterations,
            time_seconds=round(time_s, 2),
            depth=depth,
            converged=converged,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        self._data_dir.mkdir(parents=True, exist_ok=True)
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec.to_dict()) + "\n")

        # Keep in-memory cache in sync
        if self._loaded:
            self._history.append(rec)
        else:
            # Force reload on next access
            self._loaded = False

        return rec

    def load_history(self, data_dir: Optional[Path] = None) -> list[VelocityRecord]:
        """Load velocity records from JSONL file.

        Args:
            data_dir: Override data directory. If None, uses the instance default.

        Returns:
            List of VelocityRecord in chronological order.
        """
        path = (data_dir or self._data_dir) / DEFAULT_LOG_FILE
        records: list[VelocityRecord] = []

        if not path.exists():
            return records

        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(VelocityRecord.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue  # Skip malformed lines

        return records

    def avg_iterations(self, window: int = 10) -> float:
        """Rolling average iterations to converge.

        Only counts runs that actually converged.

        Args:
            window: Number of recent converged runs to average.

        Returns:
            Average iterations, or 0.0 if no converged runs.
        """
        converged = [r for r in self.history if r.converged]
        if not converged:
            return 0.0
        recent = converged[-window:]
        return round(mean(r.iterations for r in recent), 1)

    def avg_time(self, window: int = 10) -> float:
        """Rolling average time (seconds) to converge.

        Only counts runs that actually converged.

        Args:
            window: Number of recent converged runs to average.

        Returns:
            Average time in seconds, or 0.0 if no converged runs.
        """
        converged = [r for r in self.history if r.converged]
        if not converged:
            return 0.0
        recent = converged[-window:]
        return round(mean(r.time_seconds for r in recent), 1)

    def convergence_rate(self, window: int = 20) -> float:
        """Percentage of recent runs that converged.

        Args:
            window: Number of recent runs to consider.

        Returns:
            Rate between 0.0 and 1.0, or 0.0 if no history.
        """
        if not self.history:
            return 0.0
        recent = self.history[-window:]
        return round(sum(1 for r in recent if r.converged) / len(recent), 4)

    def depth_distribution(self) -> dict[int, int]:
        """Histogram of convergence depths across all runs.

        Returns:
            Dict mapping depth -> count, sorted by depth.
        """
        dist: dict[int, int] = {}
        for r in self.history:
            dist[r.depth] = dist.get(r.depth, 0) + 1
        return dict(sorted(dist.items()))

    def velocity_trend(self, window: int = 10) -> str:
        """Detect whether convergence is accelerating, stable, or decelerating.

        Compares the average iterations of the first half vs second half of
        the recent window (converged runs only).

        Args:
            window: Number of recent converged runs to analyze.

        Returns:
            One of: "accelerating", "stable", "decelerating", "insufficient_data"
        """
        converged = [r for r in self.history if r.converged]
        recent = converged[-window:]

        if len(recent) < 4:
            return "insufficient_data"

        mid = len(recent) // 2
        first_half = recent[:mid]
        second_half = recent[mid:]

        avg_first = mean(r.iterations for r in first_half)
        avg_second = mean(r.iterations for r in second_half)

        if avg_first == 0:
            return "stable"

        change_ratio = (avg_first - avg_second) / avg_first

        # Thresholds: >10% fewer iterations = accelerating,
        # >10% more = decelerating, otherwise stable
        if change_ratio > 0.10:
            return "accelerating"
        elif change_ratio < -0.10:
            return "decelerating"
        else:
            return "stable"

    def format_dashboard(self) -> str:
        """Pretty ASCII dashboard showing convergence velocity metrics.

        Sections:
        - Header with summary stats
        - Convergence rate sparkline over time
        - Average iterations trend
        - Depth distribution bar chart
        - Recent runs table (last 10)

        Returns:
            Multi-line formatted string.
        """
        if not self.history:
            return "No convergence data recorded yet."

        lines: list[str] = []
        total = len(self.history)
        rate = self.convergence_rate()
        avg_it = self.avg_iterations()
        avg_t = self.avg_time()
        trend = self.velocity_trend()

        trend_arrow = {"accelerating": "^", "decelerating": "v", "stable": "="}
        trend_sym = trend_arrow.get(trend, "?")

        # Header
        lines.append("=" * 52)
        lines.append("  CONVERGENCE VELOCITY DASHBOARD")
        lines.append("=" * 52)
        lines.append("")
        lines.append(f"  Total runs:        {total}")
        lines.append(f"  Convergence rate:  {rate * 100:.1f}%")
        lines.append(f"  Avg iterations:    {avg_it} (converged runs)")
        lines.append(f"  Avg time:          {avg_t}s (converged runs)")
        lines.append(f"  Trend:             {trend} {trend_sym}")
        lines.append("")

        # Convergence rate sparkline (rolling windows of 5)
        lines.append("  Rate over time:")
        if total >= 5:
            window_rates: list[float] = []
            for i in range(4, total):
                chunk = self.history[i - 4 : i + 1]
                r = sum(1 for x in chunk if x.converged) / len(chunk)
                window_rates.append(r)
            spark = _sparkline(window_rates, width=40)
            lines.append(f"  {spark}")
        else:
            lines.append("  (need >= 5 runs for sparkline)")
        lines.append("")

        # Depth distribution
        dist = self.depth_distribution()
        if dist:
            lines.append("  Depth distribution:")
            max_count = max(dist.values())
            for depth, count in dist.items():
                lines.append(_bar(f"d={depth}", count, max_count))
            lines.append("")

        # Recent runs table (last 10)
        recent = self.history[-10:]
        lines.append("  Recent runs (last 10):")
        lines.append(f"  {'Hash':<10s} {'It':>3s} {'Time':>7s} {'Dp':>3s} {'Ok':>3s}")
        lines.append("  " + "-" * 30)
        for r in recent:
            ok = "Y" if r.converged else "N"
            lines.append(
                f"  {r.task_hash:<10s} {r.iterations:>3d} {r.time_seconds:>6.1f}s {r.depth:>3d} {ok:>3s}"
            )
        lines.append("")
        lines.append("=" * 52)

        return "\n".join(lines)

    def format_compact(self) -> str:
        """Single-line format for statusline integration.

        Format: conv:85% 3.2it 45.0s ^

        The arrow indicates trend: ^ accelerating, v decelerating, = stable, ? unknown.

        Returns:
            Compact string suitable for tmux/shell statusline.
        """
        if not self.history:
            return "conv:--"

        rate = self.convergence_rate()
        avg_it = self.avg_iterations()
        avg_t = self.avg_time()
        trend = self.velocity_trend()

        trend_arrow = {
            "accelerating": "^",
            "decelerating": "v",
            "stable": "=",
            "insufficient_data": "?",
        }
        sym = trend_arrow.get(trend, "?")

        return f"conv:{rate * 100:.0f}% {avg_it}it {avg_t}s {sym}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    """CLI entry point for direct invocation."""
    import argparse

    parser = argparse.ArgumentParser(description="Convergence velocity tracker")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("dashboard", help="Show full ASCII dashboard")
    sub.add_parser("compact", help="Show compact statusline")

    rec = sub.add_parser("record", help="Record a convergence run")
    rec.add_argument("--task", required=True, help="Task description")
    rec.add_argument("--iterations", type=int, required=True)
    rec.add_argument("--time", type=float, required=True, help="Time in seconds")
    rec.add_argument("--depth", type=int, required=True)
    rec.add_argument("--converged", action="store_true")

    args = parser.parse_args()
    tracker = VelocityTracker()

    if args.command == "dashboard":
        print(tracker.format_dashboard())
    elif args.command == "compact":
        print(tracker.format_compact())
    elif args.command == "record":
        r = tracker.record(
            task=args.task,
            iterations=args.iterations,
            time_s=args.time,
            depth=args.depth,
            converged=args.converged,
        )
        print(f"Recorded: {r.task_hash} ({r.iterations}it, {r.time_seconds}s, d={r.depth}, {'OK' if r.converged else 'FAIL'})")
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli_main()
