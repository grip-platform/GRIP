"""Session Analytics -- productivity trends from real session data.

Scans Claude Code session JSONL files to compute message counts, durations,
tool usage, and streak data.  Supplements with git log for commit/PR counts.
Emits a concise 1-2 line [ANALYTICS] banner at session start.

Data sources (all offline-friendly):
  - ~/.claude/projects/{encoded}/*.jsonl  -- session message logs
  - git log --oneline --since='7 days ago' -- commit counts
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


@dataclass
class SessionStats:
    """Aggregated statistics across recent sessions."""

    total_sessions: int = 0
    avg_messages: float = 0.0
    avg_duration_minutes: float = 0.0
    commits_last_week: int = 0
    prs_last_week: int = 0
    top_skills: list[str] = field(default_factory=list)
    streak_days: int = 0
    productivity_trend: str = "stable"  # "up", "down", "stable"


# ---------------------------------------------------------------------------
# Session file scanning
# ---------------------------------------------------------------------------

def _find_project_dirs() -> list[Path]:
    """Find all project directories under ~/.claude/projects/."""
    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.is_dir():
        return []
    return [d for d in projects_dir.iterdir() if d.is_dir()]


def _parse_session_file(path: Path) -> dict[str, Any] | None:
    """Extract summary stats from a single session JSONL file.

    Returns dict with keys: messages, first_ts, last_ts, tools, or None on error.
    """
    messages = 0
    first_ts: str | None = None
    last_ts: str | None = None
    tools: list[str] = []

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type")
                if entry_type in ("user", "assistant"):
                    messages += 1
                    ts = entry.get("timestamp")
                    if ts:
                        if first_ts is None:
                            first_ts = ts
                        last_ts = ts

                    # Extract tool names from assistant messages
                    if entry_type == "assistant":
                        msg = entry.get("message", {})
                        content = msg.get("content", []) if isinstance(msg, dict) else []
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "tool_use":
                                    name = block.get("name", "")
                                    if name:
                                        tools.append(name)
    except OSError:
        return None

    if messages == 0:
        return None

    return {
        "messages": messages,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "tools": tools,
    }


def _parse_iso_ts(ts: str) -> datetime | None:
    """Parse ISO 8601 timestamp to datetime."""
    try:
        # Handle trailing Z and milliseconds
        ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _duration_minutes(first_ts: str | None, last_ts: str | None) -> float:
    """Compute duration in minutes between two ISO timestamps."""
    if not first_ts or not last_ts:
        return 0.0
    start = _parse_iso_ts(first_ts)
    end = _parse_iso_ts(last_ts)
    if not start or not end:
        return 0.0
    delta = (end - start).total_seconds() / 60.0
    return max(0.0, delta)


def _compute_streak(session_dates: list[datetime]) -> int:
    """Compute consecutive days with sessions, ending at the most recent day."""
    if not session_dates:
        return 0

    unique_days = sorted({d.date() for d in session_dates}, reverse=True)
    if not unique_days:
        return 0

    streak = 1
    for i in range(1, len(unique_days)):
        if unique_days[i - 1] - unique_days[i] == timedelta(days=1):
            streak += 1
        else:
            break
    return streak


def _compute_trend(sessions: list[dict[str, Any]]) -> str:
    """Determine productivity trend by comparing recent vs older message counts.

    Splits sessions in half; if recent half averages more messages, trend is 'up'.
    """
    if len(sessions) < 4:
        return "stable"

    mid = len(sessions) // 2
    older = sessions[:mid]
    recent = sessions[mid:]

    older_avg = sum(s["messages"] for s in older) / len(older)
    recent_avg = sum(s["messages"] for s in recent) / len(recent)

    # 15% threshold to avoid noise
    if recent_avg > older_avg * 1.15:
        return "up"
    elif recent_avg < older_avg * 0.85:
        return "down"
    return "stable"


def _top_tools(all_tools: list[str], n: int = 3) -> list[str]:
    """Return the top N most-used tool names, excluding builtins."""
    # Exclude the always-present core tools to surface interesting ones
    skip = {"Read", "Bash", "Edit", "Write", "Glob", "Grep", "Task", "TaskOutput"}
    counts: dict[str, int] = {}
    for t in all_tools:
        if t not in skip:
            counts[t] = counts.get(t, 0) + 1

    # If nothing interesting, fall back to core tools
    if not counts:
        counts = {}
        for t in all_tools:
            counts[t] = counts.get(t, 0) + 1

    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [name for name, _ in ranked[:n]]


# ---------------------------------------------------------------------------
# Git stats
# ---------------------------------------------------------------------------

def load_git_stats(repo_path: str = ".", days: int = 7) -> dict[str, int]:
    """Count commits and PRs from git log."""
    try:
        result = subprocess.run(
            ["git", "log", f"--since={days} days ago", "--format=%s"],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=10,
        )
        if result.returncode != 0:
            return {"commits": 0, "prs": 0}
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {"commits": 0, "prs": 0}

    commits = 0
    prs = 0
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        commits += 1
        # Detect merge-PR commits (GitHub standard format)
        if re.match(r"Merge pull request #\d+", line):
            prs += 1

    return {"commits": commits, "prs": prs}


# ---------------------------------------------------------------------------
# Main compute + format
# ---------------------------------------------------------------------------

def compute_session_stats(limit: int = 10) -> SessionStats:
    """Analyze recent sessions across all projects.

    Scans the most recent `limit` session JSONL files by modification time.
    """
    stats = SessionStats()

    # Gather all session JSONL files across projects
    all_jsonl: list[Path] = []
    for proj_dir in _find_project_dirs():
        for f in proj_dir.iterdir():
            if f.suffix == ".jsonl" and f.stat().st_size > 0:
                all_jsonl.append(f)

    if not all_jsonl:
        return stats

    # Sort by modification time (most recent last) and take the last `limit`
    all_jsonl.sort(key=lambda p: p.stat().st_mtime)
    recent_files = all_jsonl[-limit:]

    # Parse each session
    sessions: list[dict[str, Any]] = []
    all_tools: list[str] = []
    session_dates: list[datetime] = []

    for f in recent_files:
        parsed = _parse_session_file(f)
        if parsed is None:
            continue
        sessions.append(parsed)
        all_tools.extend(parsed["tools"])
        if parsed["first_ts"]:
            dt = _parse_iso_ts(parsed["first_ts"])
            if dt:
                session_dates.append(dt)

    stats.total_sessions = len(sessions)
    if not sessions:
        return stats

    # Averages
    total_msgs = sum(s["messages"] for s in sessions)
    stats.avg_messages = round(total_msgs / len(sessions), 1)

    durations = [_duration_minutes(s["first_ts"], s["last_ts"]) for s in sessions]
    valid_durations = [d for d in durations if d > 0]
    if valid_durations:
        stats.avg_duration_minutes = round(
            sum(valid_durations) / len(valid_durations), 1
        )

    # Git stats
    claude_dir_str = str(CLAUDE_DIR)
    git_data = load_git_stats(claude_dir_str)
    stats.commits_last_week = git_data["commits"]
    stats.prs_last_week = git_data["prs"]

    # Top tools/skills
    stats.top_skills = _top_tools(all_tools, 3)

    # Streak
    stats.streak_days = _compute_streak(session_dates)

    # Trend
    stats.productivity_trend = _compute_trend(sessions)

    return stats


def format_banner(stats: SessionStats) -> str:
    """Format analytics as a concise 1-2 line startup banner.

    Target: under 120 chars per line for terminal readability.
    """
    if stats.total_sessions == 0:
        return "[ANALYTICS] No session history yet"

    # Line 1: session summary
    trend_arrow = {"up": "UP", "down": "DOWN", "stable": "--"}.get(
        stats.productivity_trend, "--"
    )

    parts = [
        f"Last {stats.total_sessions} sessions:",
        f"avg {stats.avg_messages:.0f} msgs",
    ]

    if stats.avg_duration_minutes > 0:
        parts.append(f"{stats.avg_duration_minutes:.0f}min avg")

    if stats.commits_last_week > 0:
        parts.append(f"{stats.commits_last_week} commits/wk")

    if stats.streak_days > 0:
        parts.append(f"{stats.streak_days}-day streak")

    parts.append(f"trend {trend_arrow}")

    # Join with comma-space but the first part already has a colon
    line1 = f"[ANALYTICS] {parts[0]} {', '.join(parts[1:])}"

    # Line 2 (optional): top skills if interesting
    if stats.top_skills:
        skills_str = ", ".join(stats.top_skills[:3])
        line2 = f"[ANALYTICS] Top tools: {skills_str}"
        if stats.prs_last_week > 0:
            line2 += f" | {stats.prs_last_week} PRs/wk"
        return f"{line1}\n{line2}"

    return line1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import dataclasses

    parser = argparse.ArgumentParser(description="GRIP Session Analytics")
    parser.add_argument(
        "command",
        choices=["banner"],
        nargs="?",
        default="banner",
        help="Output mode",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output raw stats as JSON"
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="Number of recent sessions to analyze"
    )
    args = parser.parse_args()

    stats = compute_session_stats(limit=args.limit)

    if args.json:
        print(json.dumps(dataclasses.asdict(stats), indent=2))
    elif args.command == "banner":  # route on the command so future modes get explicit branches
        banner = format_banner(stats)
        if banner:
            print(banner)


if __name__ == "__main__":
    main()
