#!/usr/bin/env python3
"""Session energy profiler: detects energy patterns from tool usage cadence.

Analyzes session transcripts to classify energy profiles, calculate cadence
metrics, render sparklines, and find optimal working hours.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from statistics import mean, stdev
from typing import Any


class EnergyProfile(Enum):
    """Session energy classification."""
    BURST = "BURST"
    SUSTAIN = "SUSTAIN"
    CRESCENDO = "CRESCENDO"
    OSCILLATING = "OSCILLATING"
    MARATHON = "MARATHON"
    SPRINT = "SPRINT"


SPARKLINE_CHARS = "▁▂▃▄▅▆▇█"


def parse_timestamp(ts: str) -> datetime:
    """Parse ISO 8601 timestamp string."""
    # Support both with and without microseconds
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {ts}")


def calculate_cadence(events: list[dict]) -> dict[str, Any]:
    """Calculate tools-per-minute cadence and inter-tool gaps.

    Args:
        events: List of dicts with 'timestamp', 'tool', 'success' keys.

    Returns:
        Dict with tools_per_minute, inter_tool_gaps, total_duration_minutes,
        error_rate, commit_velocity.
    """
    if not events:
        return {
            "tools_per_minute": [],
            "inter_tool_gaps": [],
            "total_duration_minutes": 0.0,
            "error_rate": [],
            "commit_velocity": 0.0,
        }

    timestamps = [parse_timestamp(e["timestamp"]) for e in events]
    timestamps.sort()

    if len(timestamps) == 1:
        return {
            "tools_per_minute": [1.0],
            "inter_tool_gaps": [],
            "total_duration_minutes": 0.0,
            "error_rate": [0.0 if events[0].get("success", True) else 1.0],
            "commit_velocity": 0.0,
        }

    start = timestamps[0]
    end = timestamps[-1]
    total_seconds = (end - start).total_seconds()
    total_minutes = total_seconds / 60.0

    # Tools per minute in 1-minute windows
    tools_per_minute = []
    error_rate = []
    window_start = start
    while window_start <= end:
        window_end = window_start + timedelta(minutes=1)
        window_events = [
            e for e, t in zip(events, timestamps)
            if window_start <= t < window_end
        ]
        tools_per_minute.append(float(len(window_events)))
        if window_events:
            errors = sum(1 for e in window_events if not e.get("success", True))
            error_rate.append(errors / len(window_events))
        else:
            error_rate.append(0.0)
        window_start = window_end

    # If the last window is partial but has events not yet counted, ensure coverage
    # (the while loop already handles this via <= end)

    # Inter-tool gaps in seconds
    inter_tool_gaps = [
        (timestamps[i + 1] - timestamps[i]).total_seconds()
        for i in range(len(timestamps) - 1)
    ]

    # Commit velocity (commits per hour) - count Bash events with "commit" as proxy
    commit_count = sum(
        1 for e in events
        if e.get("tool") == "Bash" and "commit" in str(e.get("metadata", ""))
    )
    commit_velocity = (commit_count / total_minutes * 60.0) if total_minutes > 0 else 0.0

    return {
        "tools_per_minute": tools_per_minute,
        "inter_tool_gaps": inter_tool_gaps,
        "total_duration_minutes": total_minutes,
        "error_rate": error_rate,
        "commit_velocity": commit_velocity,
    }


def detect_profile(metrics: dict[str, Any]) -> EnergyProfile:
    """Classify session into an energy profile based on cadence metrics.

    Decision order matters -- more specific profiles checked first.

    Args:
        metrics: Output from calculate_cadence().

    Returns:
        EnergyProfile enum value.
    """
    tpm = metrics.get("tools_per_minute", [])
    duration = metrics.get("total_duration_minutes", 0.0)

    if not tpm:
        return EnergyProfile.SUSTAIN  # degenerate: no data = flat

    tpm_mean = mean(tpm)

    # SPRINT: short intense burst (<15 min, mean > 12)
    if duration < 15 and tpm_mean > 12:
        return EnergyProfile.SPRINT

    # MARATHON: long sustained effort (>60 min, mean > 4)
    if duration > 60 and tpm_mean > 4:
        return EnergyProfile.MARATHON

    # Need at least a few windows for pattern detection
    if len(tpm) < 4:
        # Fallback for very short sessions
        if tpm_mean > 10:
            return EnergyProfile.BURST
        return EnergyProfile.SUSTAIN

    quarter = max(1, len(tpm) // 4)
    first_quarter = tpm[:quarter]
    last_quarter = tpm[-quarter:]
    first_quarter_mean = mean(first_quarter)
    last_quarter_mean = mean(last_quarter)

    # BURST: high start (>10 tools/min first 10min), rapid fade (<3 last quarter)
    first_10min = tpm[:min(10, len(tpm))]
    first_10min_mean = mean(first_10min)
    if first_10min_mean > 10 and last_quarter_mean < 3:
        return EnergyProfile.BURST

    # CRESCENDO: slow start (<3 first quarter), building peak (>8 last quarter)
    if first_quarter_mean < 3 and last_quarter_mean > 8:
        return EnergyProfile.CRESCENDO

    # OSCILLATING: alternating high/low (>3 peaks and valleys)
    peaks, valleys = _count_peaks_valleys(tpm)
    if peaks >= 3 and valleys >= 3:
        return EnergyProfile.OSCILLATING

    # SUSTAIN: steady throughput (stdev < 2)
    if len(tpm) >= 2:
        tpm_stdev = stdev(tpm)
        if tpm_stdev < 2:
            return EnergyProfile.SUSTAIN

    # Default fallback -- pick closest match
    if first_10min_mean > last_quarter_mean * 2:
        return EnergyProfile.BURST
    if last_quarter_mean > first_quarter_mean * 2:
        return EnergyProfile.CRESCENDO

    return EnergyProfile.SUSTAIN


def _count_peaks_valleys(values: list[float], threshold: float = 2.0) -> tuple[int, int]:
    """Count significant peaks and valleys in a time series.

    A peak is a local maximum where the value exceeds both neighbors by threshold.
    A valley is a local minimum where both neighbors exceed it by threshold.
    """
    if len(values) < 3:
        return 0, 0

    peaks = 0
    valleys = 0
    for i in range(1, len(values) - 1):
        prev_val, cur, next_val = values[i - 1], values[i], values[i + 1]
        if cur - prev_val >= threshold and cur - next_val >= threshold:
            peaks += 1
        if prev_val - cur >= threshold and next_val - cur >= threshold:
            valleys += 1

    return peaks, valleys


def sparkline(values: list[float]) -> str:
    """Render a list of values as an ASCII sparkline.

    Maps values to 8 levels using SPARKLINE_CHARS (▁▂▃▄▅▆▇█).

    Args:
        values: Numeric values to visualize.

    Returns:
        String of sparkline characters.
    """
    if not values:
        return ""

    min_val = min(values)
    max_val = max(values)
    val_range = max_val - min_val

    if val_range == 0:
        # All values identical -- use middle char
        return SPARKLINE_CHARS[3] * len(values)

    chars = []
    for v in values:
        # Normalize to 0-7 index
        idx = int((v - min_val) / val_range * 7)
        idx = min(7, max(0, idx))
        chars.append(SPARKLINE_CHARS[idx])

    return "".join(chars)


def analyze_session(events: list[dict]) -> dict[str, Any]:
    """Full session analysis: cadence, profile, sparkline.

    Args:
        events: List of event dicts with timestamp, tool, success.

    Returns:
        Dict with metrics, profile, sparkline, and summary data.
    """
    metrics = calculate_cadence(events)
    profile = detect_profile(metrics)
    tpm_sparkline = sparkline(metrics["tools_per_minute"])

    return {
        "metrics": metrics,
        "profile": profile,
        "sparkline": tpm_sparkline,
        "event_count": len(events),
    }


def find_optimal_hours(sessions: list[list[dict]]) -> dict[str, Any]:
    """Determine optimal working hours from multiple sessions.

    Analyzes when the user achieves highest throughput and lowest error rates.

    Args:
        sessions: List of session event lists.

    Returns:
        Dict with hour_productivity (0-23 mapping), best_hours, worst_hours.
    """
    hour_tools = defaultdict(list)  # hour -> [tools_per_minute values]
    hour_errors = defaultdict(list)

    for session_events in sessions:
        if not session_events:
            continue
        for event in session_events:
            ts = parse_timestamp(event["timestamp"])
            hour = ts.hour
            hour_tools[hour].append(1)  # one tool call
            hour_errors[hour].append(0 if event.get("success", True) else 1)

    if not hour_tools:
        return {"hour_productivity": {}, "best_hours": [], "worst_hours": []}

    # Productivity = total tool calls in that hour across sessions
    hour_productivity = {}
    for h in range(24):
        calls = len(hour_tools.get(h, []))
        errors = sum(hour_errors.get(h, []))
        total = calls
        err_rate = errors / total if total > 0 else 0.0
        # Score: calls * (1 - error_rate) -- reward volume, penalize errors
        hour_productivity[h] = round(total * (1 - err_rate), 2)

    sorted_hours = sorted(hour_productivity.items(), key=lambda x: x[1], reverse=True)
    nonzero = [(h, s) for h, s in sorted_hours if s > 0]

    best_hours = [h for h, _ in nonzero[:3]] if nonzero else []
    worst_hours = [h for h, _ in reversed(nonzero[-3:])] if len(nonzero) >= 3 else []

    return {
        "hour_productivity": hour_productivity,
        "best_hours": best_hours,
        "worst_hours": worst_hours,
    }


def energy_summary(session: dict[str, Any]) -> str:
    """Generate a human-readable energy report.

    Args:
        session: Output from analyze_session().

    Returns:
        Multi-line string report.
    """
    metrics = session["metrics"]
    profile = session["profile"]
    tpm = metrics["tools_per_minute"]
    duration = metrics["total_duration_minutes"]
    gaps = metrics["inter_tool_gaps"]

    lines = []
    lines.append(f"Energy Profile: {profile.value}")
    lines.append(f"Duration: {duration:.1f} minutes")
    lines.append(f"Total events: {session['event_count']}")

    if tpm:
        lines.append(f"Cadence (tools/min): mean={mean(tpm):.1f}, "
                      f"min={min(tpm):.0f}, max={max(tpm):.0f}")
        if len(tpm) >= 2:
            lines.append(f"Cadence stdev: {stdev(tpm):.2f}")
    else:
        lines.append("Cadence: no data")

    if gaps:
        lines.append(f"Inter-tool gaps: mean={mean(gaps):.1f}s, "
                      f"max={max(gaps):.1f}s")

    error_rates = metrics.get("error_rate", [])
    if error_rates:
        overall_err = mean(error_rates)
        lines.append(f"Error rate: {overall_err:.1%}")

    if session["sparkline"]:
        lines.append(f"Sparkline: {session['sparkline']}")

    # Profile-specific insight
    insights = {
        EnergyProfile.BURST: "High energy start with fade. Consider breaking into shorter sessions.",
        EnergyProfile.SUSTAIN: "Steady throughput. This is your sustainable pace.",
        EnergyProfile.CRESCENDO: "Slow warm-up, strong finish. Allow ramp-up time in planning.",
        EnergyProfile.OSCILLATING: "Alternating focus/rest. Natural rhythm -- embrace the valleys.",
        EnergyProfile.MARATHON: "Extended sustained effort. Watch for fatigue in later hours.",
        EnergyProfile.SPRINT: "Short intense burst. Effective for focused tasks.",
    }
    lines.append(f"Insight: {insights[profile]}")

    return "\n".join(lines)


def load_events(filepath: str) -> list[dict]:
    """Load events from a JSONL file (one JSON object per line)."""
    events = []
    path = Path(filepath)
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def load_all_sessions(directory: str) -> list[list[dict]]:
    """Load all session files from a directory."""
    sessions = []
    dirpath = Path(directory)
    for filepath in sorted(dirpath.glob("*.jsonl")):
        sessions.append(load_events(str(filepath)))
    return sessions


def cmd_profile(args: argparse.Namespace) -> None:
    """Handle 'profile' subcommand."""
    events = load_events(args.session_file)
    result = analyze_session(events)
    print(energy_summary(result))


def cmd_history(args: argparse.Namespace) -> None:
    """Handle 'history' subcommand."""
    sessions = load_all_sessions(args.directory)
    if not sessions:
        print("No session files found.")
        return

    for i, session_events in enumerate(sessions, 1):
        result = analyze_session(session_events)
        profile = result["profile"].value
        duration = result["metrics"]["total_duration_minutes"]
        tpm = result["metrics"]["tools_per_minute"]
        tpm_mean = mean(tpm) if tpm else 0
        spark = result["sparkline"]
        print(f"Session {i:3d}: {profile:12s} | {duration:6.1f}min | "
              f"mean={tpm_mean:5.1f} t/m | {spark}")


def cmd_optimal_hours(args: argparse.Namespace) -> None:
    """Handle 'optimal-hours' subcommand."""
    sessions = load_all_sessions(args.directory)
    if not sessions:
        print("No session files found.")
        return

    result = find_optimal_hours(sessions)
    if not result["best_hours"]:
        print("Not enough data to determine optimal hours.")
        return

    print("Productivity by hour:")
    prod = result["hour_productivity"]
    max_prod = max(prod.values()) if prod else 1
    for h in range(24):
        score = prod.get(h, 0)
        bar_len = int(score / max_prod * 30) if max_prod > 0 else 0
        bar = "#" * bar_len
        print(f"  {h:02d}:00  {bar} ({score:.0f})")

    print(f"\nBest hours: {', '.join(f'{h:02d}:00' for h in result['best_hours'])}")
    if result["worst_hours"]:
        print(f"Worst hours: {', '.join(f'{h:02d}:00' for h in result['worst_hours'])}")


def cmd_sparkline(args: argparse.Namespace) -> None:
    """Handle 'sparkline' subcommand."""
    events = load_events(args.session_file)
    metrics = calculate_cadence(events)
    tpm = metrics["tools_per_minute"]
    print(sparkline(tpm))


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="session_energy",
        description="Session energy profiler: detect energy patterns from tool usage cadence.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # profile
    p_profile = subparsers.add_parser("profile", help="Analyze a session file")
    p_profile.add_argument("session_file", help="Path to session JSONL file")

    # history
    p_history = subparsers.add_parser("history", help="Show history of all sessions")
    p_history.add_argument(
        "directory", nargs="?", default=".", help="Directory with session JSONL files"
    )

    # optimal-hours
    p_optimal = subparsers.add_parser("optimal-hours", help="Find optimal working hours")
    p_optimal.add_argument(
        "directory", nargs="?", default=".", help="Directory with session JSONL files"
    )

    # sparkline
    p_sparkline = subparsers.add_parser("sparkline", help="Show sparkline for a session")
    p_sparkline.add_argument("session_file", help="Path to session JSONL file")

    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "profile": cmd_profile,
        "history": cmd_history,
        "optimal-hours": cmd_optimal_hours,
        "sparkline": cmd_sparkline,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
