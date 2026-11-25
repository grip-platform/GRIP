#!/usr/bin/env python3
"""
efficiency_scorer.py -- Quantify session efficiency with multi-dimensional metrics.

Measures 5 dimensions of session productivity:
  1. tool_efficiency  (0.25) - productive tool calls / total messages
  2. drift_penalty    (0.20) - topic switches without completion markers
  3. velocity         (0.25) - commits per hour
  4. test_ratio       (0.15) - test files / code files created
  5. converge_quality (0.15) - avg contraction ratio from converge runs

CLI:
    python3 lib/efficiency_scorer.py score [session_path]
    python3 lib/efficiency_scorer.py trend --last 10
    python3 lib/efficiency_scorer.py score --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# Optional import for converge quality
try:
    from lib.contraction_fitness import convergence_quality as _convergence_quality
    HAS_CONTRACTION = True
except ImportError:
    HAS_CONTRACTION = False


PRODUCTIVE_TOOLS = {"Read", "Edit", "Write", "Bash", "Grep", "Glob"}
COMPLETION_MARKERS = {"commit", "pr", "merge", "pushed", "deployed", "fixed"}


@dataclass
class MetricScore:
    """Single dimension score."""
    name: str
    value: float
    weight: float
    score: float  # 0-100
    benchmark: float  # historical average


@dataclass
class EfficiencyReport:
    """Full efficiency report for a session."""
    metrics: list[MetricScore] = field(default_factory=list)
    overall_score: float = 0.0
    session_path: str = ""
    comparison: str = "average"  # above_average / below_average / average
    recommendation: str = ""


# ---------------------------------------------------------------------------
# Metric calculators
# ---------------------------------------------------------------------------

def _calc_tool_efficiency(messages: list[dict]) -> MetricScore:
    """Productive tool calls / total messages."""
    total = len(messages)
    if total == 0:
        return MetricScore("tool_efficiency", 0.0, 0.25, 0.0, 0.6)

    productive = 0
    for msg in messages:
        role = msg.get("role", "")
        if role != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            )
        else:
            text = str(content)

        for tool in PRODUCTIVE_TOOLS:
            if tool in text:
                productive += 1
                break

    value = productive / total
    score = min(100.0, value * 100.0)
    return MetricScore("tool_efficiency", round(value, 4), 0.25, round(score, 2), 0.6)


def _calc_drift_penalty(messages: list[dict]) -> MetricScore:
    """Count topic switches without completion markers."""
    drift_count = 0
    last_topic_words: set[str] = set()

    for msg in messages:
        role = msg.get("role", "")
        if role != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            )
        if not isinstance(content, str):
            content = str(content)

        words = set(re.findall(r"\b[a-z]{4,}\b", content.lower()))
        if not words:
            continue

        # Check if there was a completion marker between topic changes
        has_completion = bool(words & COMPLETION_MARKERS)

        if last_topic_words:
            overlap = len(words & last_topic_words)
            total_unique = len(words | last_topic_words)
            similarity = overlap / total_unique if total_unique > 0 else 0

            if similarity < 0.15 and not has_completion:
                drift_count += 1

        last_topic_words = words

    score = max(0.0, 100.0 - drift_count * 15.0)
    return MetricScore("drift_penalty", float(drift_count), 0.20, round(score, 2), 2.0)


def _calc_velocity(messages: list[dict]) -> MetricScore:
    """Commits per hour."""
    commit_count = 0
    timestamps: list[float] = []

    for msg in messages:
        ts = msg.get("timestamp")
        if ts is not None:
            timestamps.append(float(ts))

        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            )
        if not isinstance(content, str):
            content = str(content)

        # Count commit indicators
        if re.search(r"\bgit commit\b", content, re.IGNORECASE):
            commit_count += 1
        elif re.search(r"\bcommit [0-9a-f]{7,40}\b", content):
            commit_count += 1

    if len(timestamps) >= 2:
        duration_hours = (max(timestamps) - min(timestamps)) / 3600.0
    else:
        duration_hours = 1.0  # default assumption

    if duration_hours <= 0:
        duration_hours = 1.0

    velocity = commit_count / duration_hours
    score = min(100.0, velocity * 20.0)
    return MetricScore("velocity", round(velocity, 4), 0.25, round(score, 2), 3.0)


def _calc_test_ratio(messages: list[dict]) -> MetricScore:
    """Test files created / code files created."""
    test_files = 0
    code_files = 0

    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            )
        if not isinstance(content, str):
            content = str(content)

        # Look for file creation patterns
        created = re.findall(r"(?:Write|created?|new file)[:\s]+(\S+\.(?:py|ts|js|sh|rb|go|rs))", content, re.IGNORECASE)
        for f in created:
            basename = os.path.basename(f)
            if basename.startswith("test_") or basename.endswith("_test.py") or "spec." in basename:
                test_files += 1
            else:
                code_files += 1

    if code_files == 0:
        ratio = 1.0 if test_files > 0 else 0.0
    else:
        ratio = test_files / code_files

    score = min(100.0, ratio * 100.0)
    return MetricScore("test_ratio", round(ratio, 4), 0.15, round(score, 2), 1.0)


def _calc_converge_quality(messages: list[dict]) -> MetricScore:
    """Avg contraction ratio from recent converge runs."""
    if not HAS_CONTRACTION:
        # Graceful fallback: score based on converge mentions
        converge_count = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
            if not isinstance(content, str):
                content = str(content)
            if "converge" in content.lower():
                converge_count += 1

        score = min(100.0, converge_count * 20.0)
        return MetricScore("converge_quality", round(converge_count, 4), 0.15, round(score, 2), 3.0)

    # With contraction fitness available, analyze score sequences
    score_sequences: list[list[float]] = []
    current_seq: list[float] = []

    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            )
        if not isinstance(content, str):
            content = str(content)

        scores = re.findall(r"score[:\s]+(\d+(?:\.\d+)?)/100", content, re.IGNORECASE)
        for s in scores:
            current_seq.append(float(s))

        if "converge" in content.lower() and current_seq:
            score_sequences.append(current_seq)
            current_seq = []

    if current_seq:
        score_sequences.append(current_seq)

    if not score_sequences:
        return MetricScore("converge_quality", 0.0, 0.15, 50.0, 0.7)

    contractive_runs = 0
    total_runs = 0
    for seq in score_sequences:
        if len(seq) >= 2:
            total_runs += 1
            quality = _convergence_quality(seq)
            if quality.get("contractive", False):
                contractive_runs += 1

    if total_runs == 0:
        return MetricScore("converge_quality", 0.0, 0.15, 50.0, 0.7)

    value = contractive_runs / total_runs
    score = value * 100.0
    return MetricScore("converge_quality", round(value, 4), 0.15, round(score, 2), 0.7)


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyze_from_data(messages: list[dict]) -> EfficiencyReport:
    """Analyze efficiency from message data (no file I/O)."""
    metrics = [
        _calc_tool_efficiency(messages),
        _calc_drift_penalty(messages),
        _calc_velocity(messages),
        _calc_test_ratio(messages),
        _calc_converge_quality(messages),
    ]

    overall = sum(m.score * m.weight for m in metrics)
    overall = round(min(100.0, max(0.0, overall)), 2)

    if overall >= 75:
        recommendation = "Session highly productive. Maintain current patterns."
    elif overall >= 50:
        low_metrics = sorted(metrics, key=lambda m: m.score)[:2]
        areas = ", ".join(m.name for m in low_metrics)
        recommendation = f"Room for improvement in: {areas}."
    else:
        recommendation = "Consider reducing context switches and increasing test coverage."

    return EfficiencyReport(
        metrics=metrics,
        overall_score=overall,
        session_path="",
        comparison="average",
        recommendation=recommendation,
    )


def analyze_session(session_path: str) -> EfficiencyReport:
    """Analyze a session JSONL file."""
    messages: list[dict] = []
    path = Path(session_path)

    if path.is_file():
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    elif path.is_dir():
        # Check for conversation files in the directory
        for candidate in ["conversation.jsonl", "messages.jsonl", "session.jsonl"]:
            conv_path = path / candidate
            if conv_path.exists():
                return analyze_session(str(conv_path))
        # Try JSON files
        for json_file in sorted(path.glob("*.json")):
            try:
                with open(json_file) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    messages.extend(data)
                elif isinstance(data, dict) and "messages" in data:
                    messages.extend(data["messages"])
            except (json.JSONDecodeError, KeyError):
                continue

    report = analyze_from_data(messages)
    report.session_path = session_path
    return report


def compare_to_history(current: EfficiencyReport, history: list[EfficiencyReport]) -> str:
    """Compare current score to historical average."""
    if not history:
        return "average"

    avg = sum(r.overall_score for r in history) / len(history)
    diff = current.overall_score - avg

    if diff > 5:
        return "above_average"
    elif diff < -5:
        return "below_average"
    else:
        return "average"


def trend(projects_dir: str, last_n: int = 10) -> list[EfficiencyReport]:
    """Analyze last N sessions from the projects directory."""
    projects_path = Path(projects_dir)
    if not projects_path.exists():
        return []

    session_dirs: list[Path] = []
    for project_dir in projects_path.iterdir():
        if not project_dir.is_dir():
            continue
        # Look for session/conversation files
        for candidate in ["conversation.jsonl", "messages.jsonl", "session.jsonl"]:
            conv = project_dir / candidate
            if conv.exists():
                session_dirs.append(conv)
                break
        else:
            # Check subdirectories (sessions might be nested)
            for sub in sorted(project_dir.iterdir()):
                if sub.is_dir():
                    for candidate in ["conversation.jsonl", "messages.jsonl"]:
                        conv = sub / candidate
                        if conv.exists():
                            session_dirs.append(conv)
                            break

    # Sort by modification time, take last N
    session_dirs.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
    session_dirs = session_dirs[-last_n:]

    reports = []
    for session_file in session_dirs:
        report = analyze_session(str(session_file))
        reports.append(report)

    # Update comparison for each report
    for i, report in enumerate(reports):
        history = reports[:i]
        report.comparison = compare_to_history(report, history)

    return reports


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _bar(score: float, width: int = 20) -> str:
    """ASCII bar chart for a score 0-100."""
    filled = int(score / 100.0 * width)
    empty = width - filled
    return f"[{'#' * filled}{'.' * empty}]"


def format_report(report: EfficiencyReport) -> str:
    """Format report as ASCII table with bar charts."""
    lines = []
    lines.append("=" * 60)
    lines.append(f"  EFFICIENCY REPORT  |  Overall: {report.overall_score:.1f}/100")
    lines.append("=" * 60)

    if report.session_path:
        lines.append(f"  Session: {report.session_path}")
        lines.append("-" * 60)

    lines.append("")
    lines.append(f"  {'Metric':<20} {'Score':>6} {'Bench':>6}  {'Bar'}")
    lines.append(f"  {'-'*20} {'-'*6} {'-'*6}  {'-'*22}")

    for m in report.metrics:
        bar = _bar(m.score)
        lines.append(f"  {m.name:<20} {m.score:>5.1f}% {m.benchmark:>6.1f}  {bar}")

    lines.append("")
    lines.append(f"  Comparison: {report.comparison.replace('_', ' ')}")
    lines.append(f"  Recommendation: {report.recommendation}")
    lines.append("=" * 60)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _find_latest_session() -> Optional[str]:
    """Find the most recent session in the projects directory."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None

    latest: Optional[Path] = None
    latest_mtime = 0.0

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for candidate in ["conversation.jsonl", "messages.jsonl", "session.jsonl"]:
            conv = project_dir / candidate
            if conv.exists():
                mtime = conv.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest = conv

    return str(latest) if latest else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Quantify session efficiency",
        prog="efficiency_scorer",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    subparsers = parser.add_subparsers(dest="command")

    # score subcommand
    score_parser = subparsers.add_parser("score", help="Score a session")
    score_parser.add_argument("session_path", nargs="?", default=None, help="Path to session file/dir")
    score_parser.add_argument("--json", action="store_true", dest="json_out", help="Output as JSON")

    # trend subcommand
    trend_parser = subparsers.add_parser("trend", help="Show trend across sessions")
    trend_parser.add_argument("--last", type=int, default=10, help="Number of sessions to analyze")
    trend_parser.add_argument("--json", action="store_true", dest="json_out", help="Output as JSON")

    args = parser.parse_args()

    if args.command == "score":
        session_path = args.session_path
        if session_path is None:
            session_path = _find_latest_session()
            if session_path is None:
                print("No session found. Provide a path.", file=sys.stderr)
                sys.exit(1)

        report = analyze_session(session_path)

        if getattr(args, "json_out", False) or args.json:
            print(json.dumps(asdict(report), indent=2))
        else:
            print(format_report(report))

    elif args.command == "trend":
        projects_dir = str(Path.home() / ".claude" / "projects")
        reports = trend(projects_dir, last_n=args.last)

        if not reports:
            print("No sessions found for trend analysis.", file=sys.stderr)
            sys.exit(1)

        if getattr(args, "json_out", False) or args.json:
            print(json.dumps([asdict(r) for r in reports], indent=2))
        else:
            for i, report in enumerate(reports, 1):
                print(f"\n--- Session {i}/{len(reports)} ---")
                print(format_report(report))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
