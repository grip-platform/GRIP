#!/usr/bin/env python3
"""Real-time health scoring for Claude Code sessions.

Combines multiple signals (context usage, error rate, tool diversity,
commit frequency, drift) into a weighted composite health score with
configurable alert thresholds and an ASCII dashboard.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class HealthSignal:
    """A single health dimension."""
    name: str
    value: float          # 0-100
    weight: float         # relative weight
    status: str           # healthy / warning / critical
    message: str = ""


@dataclass
class Alert:
    """An alert raised when a signal crosses a threshold."""
    level: str            # info / warning / critical
    message: str
    signal_name: str
    threshold: float
    actual_value: float


@dataclass
class SessionHealth:
    """Aggregate health snapshot."""
    overall_score: float
    signals: List[HealthSignal] = field(default_factory=list)
    alerts: List[Alert] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Signal weights
# ---------------------------------------------------------------------------

SIGNAL_WEIGHTS: Dict[str, float] = {
    "context_usage":    0.25,
    "error_rate":       0.20,
    "tool_diversity":   0.15,
    "commit_frequency": 0.15,
    "drift":            0.25,
}

# ---------------------------------------------------------------------------
# Individual signal checkers
# ---------------------------------------------------------------------------

def _status_from_value(value: float) -> str:
    if value >= 70:
        return "healthy"
    if value >= 40:
        return "warning"
    return "critical"


def check_context_usage(pct: float) -> HealthSignal:
    """Score from context-window percentage used.

    100 at 0 % used, 0 at 100 % used.  Linear interpolation.
    """
    pct = max(0.0, min(100.0, pct))
    value = 100.0 - pct
    status = _status_from_value(value)
    msg = ""
    if pct >= 85:
        msg = "Context window nearly full -- consider compacting"
    elif pct >= 70:
        msg = "Context window usage is elevated"
    return HealthSignal(
        name="context_usage",
        value=round(value, 2),
        weight=SIGNAL_WEIGHTS["context_usage"],
        status=status,
        message=msg,
    )


def check_error_rate(errors: int, total: int) -> HealthSignal:
    """Score from error ratio.

    100 at 0 % errors, 0 at >= 20 % errors.  Linear in [0, 20].
    """
    if total <= 0:
        ratio = 0.0
    else:
        ratio = errors / total
    ratio = max(0.0, min(1.0, ratio))
    # Map 0 -> 100, 0.20 -> 0, >0.20 -> 0
    value = max(0.0, 100.0 - (ratio / 0.20) * 100.0)
    status = _status_from_value(value)
    msg = ""
    if value < 40:
        msg = f"High error rate: {ratio:.0%}"
    elif value < 70:
        msg = f"Elevated error rate: {ratio:.0%}"
    return HealthSignal(
        name="error_rate",
        value=round(value, 2),
        weight=SIGNAL_WEIGHTS["error_rate"],
        status=status,
        message=msg,
    )


def check_tool_diversity(tool_counts: Dict[str, int]) -> HealthSignal:
    """Score from Shannon entropy of tool usage.

    Normalised to [0, 100] where 100 = perfectly uniform usage across
    all observed tool types and 0 = only one tool used.
    """
    if not tool_counts:
        return HealthSignal(
            name="tool_diversity",
            value=0.0,
            weight=SIGNAL_WEIGHTS["tool_diversity"],
            status="critical",
            message="No tool usage data",
        )
    total = sum(tool_counts.values())
    if total == 0:
        return HealthSignal(
            name="tool_diversity",
            value=0.0,
            weight=SIGNAL_WEIGHTS["tool_diversity"],
            status="critical",
            message="No tool usage data",
        )

    n = len(tool_counts)
    if n == 1:
        return HealthSignal(
            name="tool_diversity",
            value=0.0,
            weight=SIGNAL_WEIGHTS["tool_diversity"],
            status="critical",
            message="Only one tool type used",
        )

    entropy = -sum(
        (c / total) * math.log2(c / total)
        for c in tool_counts.values()
        if c > 0
    )
    max_entropy = math.log2(n)
    normalised = (entropy / max_entropy) if max_entropy > 0 else 0.0
    value = round(normalised * 100.0, 2)
    status = _status_from_value(value)
    msg = ""
    if value < 40:
        msg = "Very low tool diversity -- consider using more tool types"
    elif value < 70:
        msg = "Tool diversity low -- consider using more tool types"
    return HealthSignal(
        name="tool_diversity",
        value=value,
        weight=SIGNAL_WEIGHTS["tool_diversity"],
        status=status,
        message=msg,
    )


def check_commit_frequency(commits: int, duration_min: float) -> HealthSignal:
    """Score from commits per hour.

    Target: ~3 commits/hour = 100.  0 commits/hour = 0.
    Caps at 100 (no penalty for committing more).
    """
    if duration_min <= 0:
        cph = 0.0
    else:
        cph = commits / (duration_min / 60.0)

    target_cph = 3.0
    value = min(100.0, (cph / target_cph) * 100.0)
    value = round(max(0.0, value), 2)
    status = _status_from_value(value)
    msg = ""
    if value < 40:
        msg = f"Low commit frequency: {cph:.1f}/hr (target: {target_cph:.0f}/hr)"
    elif value < 70:
        msg = f"Below-target commit frequency: {cph:.1f}/hr"
    return HealthSignal(
        name="commit_frequency",
        value=value,
        weight=SIGNAL_WEIGHTS["commit_frequency"],
        status=status,
        message=msg,
    )


def check_drift(task_switches: int, completions: int) -> HealthSignal:
    """Score from completion ratio.

    If task_switches == 0: perfect (100).
    Otherwise: (completions / task_switches) * 100, capped at 100.
    Switching without finishing = drift.
    """
    if task_switches <= 0:
        value = 100.0
    else:
        ratio = completions / task_switches
        value = min(100.0, ratio * 100.0)
    value = round(max(0.0, value), 2)
    status = _status_from_value(value)
    msg = ""
    unfinished = max(0, task_switches - completions)
    if value < 40:
        msg = f"Drift detected -- {unfinished} task switches without completion"
    elif value < 70:
        msg = f"Mild drift -- {unfinished} task switches without completion"
    return HealthSignal(
        name="drift",
        value=value,
        weight=SIGNAL_WEIGHTS["drift"],
        status=status,
        message=msg,
    )


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------

def compute_health(signals: List[HealthSignal]) -> float:
    """Weighted composite score from individual signals."""
    if not signals:
        return 0.0
    total_weight = sum(s.weight for s in signals)
    if total_weight == 0:
        return 0.0
    score = sum(s.value * s.weight for s in signals) / total_weight
    return round(max(0.0, min(100.0, score)), 2)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

def default_thresholds() -> Dict[str, Dict[str, float]]:
    """Sensible default thresholds for alert generation."""
    return {
        "context_usage":    {"warning": 40, "critical": 20},
        "error_rate":       {"warning": 50, "critical": 25},
        "tool_diversity":   {"warning": 50, "critical": 25},
        "commit_frequency": {"warning": 40, "critical": 20},
        "drift":            {"warning": 50, "critical": 30},
    }


def generate_alerts(
    signals: List[HealthSignal],
    thresholds: Optional[Dict[str, Dict[str, float]]] = None,
) -> List[Alert]:
    """Produce alerts for signals below their threshold."""
    if thresholds is None:
        thresholds = default_thresholds()

    alerts: List[Alert] = []
    for sig in signals:
        t = thresholds.get(sig.name)
        if not t:
            continue
        crit = t.get("critical", 0)
        warn = t.get("warning", 0)
        if sig.value < crit:
            alerts.append(Alert(
                level="critical",
                message=sig.message or f"{sig.name} is critical",
                signal_name=sig.name,
                threshold=crit,
                actual_value=sig.value,
            ))
        elif sig.value < warn:
            alerts.append(Alert(
                level="warning",
                message=sig.message or f"{sig.name} is below warning threshold",
                signal_name=sig.name,
                threshold=warn,
                actual_value=sig.value,
            ))
    return alerts


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_LABEL_WIDTH = 18
_BAR_WIDTH = 10


def _bar(value: float) -> str:
    filled = round(value / 100.0 * _BAR_WIDTH)
    return "\u2588" * filled + "\u2591" * (_BAR_WIDTH - filled)


def _status_label(status: str) -> str:
    return {"healthy": "OK", "warning": "WARNING", "critical": "CRITICAL"}.get(
        status, status.upper()
    )


def _overall_status(score: float) -> str:
    if score >= 70:
        return "OK"
    if score >= 40:
        return "WARNING"
    return "CRITICAL"


def format_health_report(health: SessionHealth) -> str:
    """ASCII health dashboard."""
    lines: List[str] = []
    status_tag = _overall_status(health.overall_score)
    lines.append(
        f"Session Health: {health.overall_score:.0f}/100 [{status_tag}]"
    )
    lines.append("\u2500" * 38)

    display_names = {
        "context_usage": "Context Usage",
        "error_rate": "Error Rate",
        "tool_diversity": "Tool Diversity",
        "commit_frequency": "Commit Frequency",
        "drift": "Drift Score",
    }

    for sig in health.signals:
        label = display_names.get(sig.name, sig.name)
        label_padded = f"{label}:".ljust(_LABEL_WIDTH)
        bar = _bar(sig.value)
        st = _status_label(sig.status)
        lines.append(f"  {label_padded}{bar} {sig.value:>3.0f}  {st}")

    if health.alerts:
        lines.append("")
        lines.append("Alerts:")
        for a in health.alerts:
            prefix = "!!" if a.level == "critical" else "!"
            lines.append(f"  {prefix} {a.message}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience: full health check
# ---------------------------------------------------------------------------

def full_health_check(
    context_pct: float = 0.0,
    errors: int = 0,
    total_ops: int = 0,
    tool_counts: Optional[Dict[str, int]] = None,
    commits: int = 0,
    duration_min: float = 60.0,
    task_switches: int = 0,
    completions: int = 0,
    thresholds: Optional[Dict[str, Dict[str, float]]] = None,
) -> SessionHealth:
    """Run all checks and return a SessionHealth snapshot."""
    signals = [
        check_context_usage(context_pct),
        check_error_rate(errors, total_ops),
        check_tool_diversity(tool_counts or {}),
        check_commit_frequency(commits, duration_min),
        check_drift(task_switches, completions),
    ]
    overall = compute_health(signals)
    alerts = generate_alerts(signals, thresholds)
    return SessionHealth(
        overall_score=overall,
        signals=signals,
        alerts=alerts,
    )


# ---------------------------------------------------------------------------
# History persistence
# ---------------------------------------------------------------------------

_HISTORY_FILE = Path.home() / ".claude" / "cache" / "session_health_history.jsonl"


def _save_snapshot(health: SessionHealth) -> None:
    """Append a health snapshot to the history file."""
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": health.timestamp,
        "overall_score": health.overall_score,
        "signals": {s.name: s.value for s in health.signals},
        "alert_count": len(health.alerts),
    }
    with open(_HISTORY_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


def _load_history(limit: int = 20) -> List[dict]:
    """Load recent history entries."""
    if not _HISTORY_FILE.exists():
        return []
    lines = _HISTORY_FILE.read_text().strip().splitlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="session_health",
        description="Real-time session health scoring for Claude Code sessions",
    )
    sub = parser.add_subparsers(dest="command")

    # check
    check_p = sub.add_parser("check", help="Run health check (reads JSON from stdin or uses sample data)")
    check_p.add_argument("--save", action="store_true", help="Save snapshot to history")

    # history
    hist_p = sub.add_parser("history", help="Show past session scores")
    hist_p.add_argument("-n", "--limit", type=int, default=10, help="Number of entries")

    # alerts
    sub.add_parser("alerts", help="Show current alerts (reads JSON from stdin or uses sample data)")

    # thresholds
    sub.add_parser("thresholds", help="Show default alert thresholds")

    return parser


def _read_input() -> dict:
    """Read JSON input from stdin or return sample data."""
    try:
        if not sys.stdin.isatty():
            try:
                return json.load(sys.stdin)
            except (json.JSONDecodeError, ValueError):
                pass
    except OSError:
        pass
    # Sample data for demonstration
    return {
        "context_pct": 45.0,
        "errors": 2,
        "total_ops": 50,
        "tool_counts": {"Bash": 15, "Read": 20, "Edit": 10, "Grep": 8, "Write": 3},
        "commits": 3,
        "duration_min": 60.0,
        "task_switches": 4,
        "completions": 3,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "check":
        data = _read_input()
        health = full_health_check(**data)
        print(format_health_report(health))
        if args.save:
            _save_snapshot(health)
            print(f"\nSnapshot saved to {_HISTORY_FILE}")
        return 0

    elif args.command == "history":
        entries = _load_history(args.limit)
        if not entries:
            print("No history entries found.")
            return 0
        print(f"{'Timestamp':<22} {'Score':>5}  {'Alerts':>6}")
        print("-" * 38)
        for e in entries:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["timestamp"]))
            print(f"{ts:<22} {e['overall_score']:>5.0f}  {e['alert_count']:>6}")
        return 0

    elif args.command == "alerts":
        data = _read_input()
        health = full_health_check(**data)
        if not health.alerts:
            print("No alerts.")
            return 0
        for a in health.alerts:
            prefix = "CRITICAL" if a.level == "critical" else "WARNING"
            print(f"[{prefix}] {a.signal_name}: {a.message} "
                  f"(threshold: {a.threshold}, actual: {a.actual_value:.0f})")
        return 0

    elif args.command == "thresholds":
        t = default_thresholds()
        print(f"{'Signal':<20} {'Warning':>8} {'Critical':>8}")
        print("-" * 38)
        for name, levels in sorted(t.items()):
            print(f"{name:<20} {levels['warning']:>8.0f} {levels['critical']:>8.0f}")
        return 0

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
