"""
Developer Flow Detector — detects productivity patterns from git signals.

Analyzes commit frequency, file-switch patterns, and session rhythm to
determine whether the developer is in flow state, warming up, winding down,
or struggling. Provides actionable suggestions.

Usage:
    python3 lib/flow_detector.py status          # Current flow state
    python3 lib/flow_detector.py history [--days 7] # Flow history
    python3 lib/flow_detector.py tips             # Flow optimization tips
"""

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from lib.subprocess_utils import run_git as _run_git


CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


@dataclass
class FlowState:
    """Current developer flow state."""
    state: str  # flow, warming_up, winding_down, struggling, idle, deep_focus
    confidence: float = 0.0  # 0.0 - 1.0
    duration_minutes: int = 0
    signals: list = field(default_factory=list)
    suggestion: str = ""
    commit_velocity: float = 0.0  # commits per hour
    file_focus: float = 0.0  # 0-1, higher = more focused on fewer files
    session_energy: float = 0.0  # 0-1, estimated energy level


@dataclass
class FlowSession:
    """A detected flow session from history."""
    start: str = ""
    end: str = ""
    duration_minutes: int = 0
    state: str = "idle"
    commits: int = 0
    files_touched: int = 0
    lines_changed: int = 0


@dataclass
class FlowHistory:
    """Historical flow analysis."""
    sessions: list = field(default_factory=list)
    total_flow_minutes: int = 0
    total_deep_focus_minutes: int = 0
    average_flow_duration: int = 0
    best_flow_hour: int = 0  # Hour of day with most flow
    best_flow_day: int = 0  # Day of week with most flow
    flow_ratio: float = 0.0  # % of time in flow/deep_focus


def _parse_commit_log(log: str) -> list:
    """Parse git log into structured commits."""
    if not log.strip():
        return []
    commits = []
    for line in log.strip().split("\n"):
        parts = line.split("|", 3)
        if len(parts) >= 3:
            commits.append({
                "hash": parts[0].strip(),
                "timestamp": parts[1].strip(),
                "message": parts[2].strip(),
                "files": int(parts[3].strip()) if len(parts) > 3 and parts[3].strip().isdigit() else 0,
            })
    return commits


def analyze_commit_velocity(hours: int = 4) -> dict:
    """Analyze commit velocity over recent hours."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    log = _run_git([
        "log", "--format=%h|%aI|%s", f"--since={since}", "--no-merges",
    ])
    commits = _parse_commit_log(log)

    if not commits:
        return {"count": 0, "per_hour": 0.0, "intervals": [], "burst": False}

    # Calculate intervals between commits
    intervals = []
    timestamps = []
    for c in commits:
        try:
            ts = datetime.fromisoformat(c["timestamp"])
            timestamps.append(ts)
        except (ValueError, KeyError):
            continue

    timestamps.sort()
    for i in range(1, len(timestamps)):
        delta = (timestamps[i] - timestamps[i - 1]).total_seconds() / 60.0
        intervals.append(delta)

    per_hour = len(commits) / max(hours, 1)
    burst = len(intervals) >= 2 and sum(1 for i in intervals if i < 10) >= len(intervals) * 0.6

    return {
        "count": len(commits),
        "per_hour": round(per_hour, 2),
        "intervals": intervals,
        "burst": burst,
    }


def analyze_file_focus(hours: int = 2) -> dict:
    """Analyze file focus — how scattered vs. focused the work is."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    log = _run_git(["log", "--format=", "--name-only", f"--since={since}", "--no-merges"])

    if not log.strip():
        return {"focus_score": 0.0, "unique_files": 0, "total_touches": 0, "primary_dir": ""}

    files = [f for f in log.strip().split("\n") if f.strip()]
    unique_files = set(files)
    total_touches = len(files)

    # Focus = 1 - (unique / total), high = focused on few files repeatedly
    focus = 1.0 - (len(unique_files) / max(total_touches, 1)) if total_touches > 1 else 0.0

    # Find primary directory
    dirs = [str(Path(f).parent) for f in unique_files if "/" in f]
    primary_dir = max(set(dirs), key=dirs.count) if dirs else ""

    return {
        "focus_score": round(min(focus, 1.0), 2),
        "unique_files": len(unique_files),
        "total_touches": total_touches,
        "primary_dir": primary_dir,
    }


def analyze_session_rhythm() -> dict:
    """Analyze the session rhythm — commit timing patterns."""
    log = _run_git(["log", "--format=%aI", "--since=24 hours ago", "--no-merges"])

    if not log.strip():
        return {"active_hours": [], "peak_hour": -1, "rhythm": "none"}

    timestamps = []
    for line in log.strip().split("\n"):
        try:
            timestamps.append(datetime.fromisoformat(line.strip()))
        except ValueError:
            continue

    if not timestamps:
        return {"active_hours": [], "peak_hour": -1, "rhythm": "none"}

    hours = [ts.hour for ts in timestamps]
    hour_counts = {}
    for h in hours:
        hour_counts[h] = hour_counts.get(h, 0) + 1

    peak_hour = max(hour_counts, key=hour_counts.get) if hour_counts else -1
    active_hours = sorted(hour_counts.keys())

    # Determine rhythm pattern
    if len(active_hours) <= 2:
        rhythm = "burst"
    elif max(active_hours) - min(active_hours) <= 4:
        rhythm = "focused"
    elif len(active_hours) >= 8:
        rhythm = "sustained"
    else:
        rhythm = "sporadic"

    return {
        "active_hours": active_hours,
        "peak_hour": peak_hour,
        "rhythm": rhythm,
    }


def detect_flow_state(
    velocity: dict = None,
    focus: dict = None,
    rhythm: dict = None,
) -> FlowState:
    """Detect current flow state from signals."""
    if velocity is None:
        velocity = analyze_commit_velocity()
    if focus is None:
        focus = analyze_file_focus()
    if rhythm is None:
        rhythm = analyze_session_rhythm()

    signals = []
    state = "idle"
    confidence = 0.0
    suggestion = ""

    commits_per_hour = velocity.get("per_hour", 0)
    focus_score = focus.get("focus_score", 0)
    is_burst = velocity.get("burst", False)
    rhythm_type = rhythm.get("rhythm", "none")

    # Deep Focus: high velocity + high focus + burst pattern
    if commits_per_hour >= 3 and focus_score >= 0.5 and is_burst:
        state = "deep_focus"
        confidence = min(0.5 + commits_per_hour * 0.1 + focus_score * 0.3, 1.0)
        signals.append("High commit velocity with focused file changes")
        signals.append("Burst commit pattern detected")
        suggestion = "You're in deep focus. Avoid interruptions. Consider silencing notifications."

    # Flow: good velocity + reasonable focus
    elif commits_per_hour >= 2 and focus_score >= 0.3:
        state = "flow"
        confidence = min(0.4 + commits_per_hour * 0.1 + focus_score * 0.2, 0.95)
        signals.append("Steady commit velocity")
        if focus_score >= 0.5:
            signals.append("Focused on specific files")
        suggestion = "Good flow. Keep the momentum going."

    # Struggling: activity but scattered (check BEFORE warming_up — more specific)
    elif commits_per_hour >= 0.5 and focus_score < 0.2 and not is_burst:
        state = "struggling"
        confidence = 0.6
        signals.append("Scattered file changes suggest context-switching")
        if velocity.get("count", 0) > 0:
            intervals = velocity.get("intervals", [])
            long_gaps = [i for i in intervals if i > 30]
            if long_gaps:
                signals.append(f"{len(long_gaps)} gaps > 30 minutes detected")
        suggestion = "Consider breaking down the current task into smaller steps."

    # Warming Up: some activity, building momentum
    elif commits_per_hour >= 0.5 and commits_per_hour < 2:
        state = "warming_up"
        confidence = 0.5 + commits_per_hour * 0.1
        signals.append("Building commit momentum")
        if focus_score < 0.3:
            signals.append("Exploring multiple files")
        suggestion = "Still warming up. Pick one task and focus to enter flow."

    # Winding Down: decreasing activity
    elif commits_per_hour > 0 and commits_per_hour < 0.5:
        state = "winding_down"
        confidence = 0.5
        signals.append("Commit velocity declining")
        suggestion = "Good time to wrap up, commit remaining work, and document progress."

    # Idle: no recent activity
    else:
        state = "idle"
        confidence = 0.8
        signals.append("No recent commit activity")
        suggestion = "Ready for a new task? Check the command palette for suggestions."

    return FlowState(
        state=state,
        confidence=round(confidence, 2),
        duration_minutes=0,  # Would need session tracking for actual duration
        signals=signals,
        suggestion=suggestion,
        commit_velocity=commits_per_hour,
        file_focus=focus_score,
        session_energy=min(commits_per_hour / 4.0, 1.0),
    )


def get_flow_tips(state: FlowState) -> list:
    """Get actionable tips based on current flow state."""
    tips = {
        "deep_focus": [
            "Block all notifications for the next hour",
            "Set a timer to remind you to take a break in 90 minutes",
            "Avoid starting new features until current one is committed",
            "This is your peak productivity — tackle the hardest problem now",
        ],
        "flow": [
            "Great momentum! Avoid context-switching",
            "Consider time-boxing your current task to maintain intensity",
            "Queue up your next task so you don't lose momentum after committing",
            "Take a 5-minute break every 45 minutes to sustain flow",
        ],
        "warming_up": [
            "Start with a small, easy win to build momentum",
            "Review your last session's 'What's Up Next' to pick up where you left off",
            "Close unnecessary browser tabs and IDE windows",
            "Try the Pomodoro technique: 25 minutes focused, 5 minutes break",
        ],
        "struggling": [
            "Break the current problem into smaller pieces",
            "Write a failing test first to clarify what you're building",
            "Take a 10-minute walk to clear your head",
            "Pair with someone or use /broly fork for a fresh perspective",
            "Check if you're yak-shaving — is this the real task?",
        ],
        "winding_down": [
            "Commit all current work before stopping",
            "Write a brief 'What's Up Next' note for tomorrow",
            "Run the test suite to ensure everything is green",
            "Generate a standup summary for tomorrow's standup",
        ],
        "idle": [
            "Check the command palette (/palette) for suggested next actions",
            "Review open PRs that need your attention",
            "Pick up the highest-priority item from the backlog",
            "Consider doing a code review to warm up",
        ],
    }
    return tips.get(state.state, tips["idle"])


# === History Analysis ===

def analyze_flow_history(days: int = 7) -> FlowHistory:
    """Analyze flow patterns over recent days."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    log = _run_git([
        "log", "--format=%aI|%s", f"--since={since}", "--no-merges",
    ])

    if not log.strip():
        return FlowHistory()

    # Group commits by hour blocks
    hour_blocks = {}
    for line in log.strip().split("\n"):
        parts = line.split("|", 1)
        if len(parts) < 2:
            continue
        try:
            ts = datetime.fromisoformat(parts[0].strip())
            key = ts.strftime("%Y-%m-%d %H:00")
            if key not in hour_blocks:
                hour_blocks[key] = []
            hour_blocks[key].append(ts)
        except ValueError:
            continue

    # Detect flow sessions (consecutive hours with activity)
    sessions = []
    sorted_keys = sorted(hour_blocks.keys())

    current_session = None
    for key in sorted_keys:
        commits = hour_blocks[key]
        if current_session is None:
            current_session = {
                "start": commits[0],
                "end": commits[-1],
                "commits": len(commits),
            }
        else:
            # Check if this hour is within 1 hour of the last
            last_end = current_session["end"]
            first_commit = commits[0]
            gap = (first_commit - last_end).total_seconds() / 3600.0
            if gap <= 2.0:
                current_session["end"] = commits[-1]
                current_session["commits"] += len(commits)
            else:
                # Save current session
                duration = (current_session["end"] - current_session["start"]).total_seconds() / 60
                sessions.append(FlowSession(
                    start=current_session["start"].isoformat(),
                    end=current_session["end"].isoformat(),
                    duration_minutes=int(duration),
                    state="flow" if duration >= 60 else "warming_up",
                    commits=current_session["commits"],
                ))
                current_session = {
                    "start": commits[0],
                    "end": commits[-1],
                    "commits": len(commits),
                }

    if current_session:
        duration = (current_session["end"] - current_session["start"]).total_seconds() / 60
        sessions.append(FlowSession(
            start=current_session["start"].isoformat(),
            end=current_session["end"].isoformat(),
            duration_minutes=int(duration),
            state="flow" if duration >= 60 else "warming_up",
            commits=current_session["commits"],
        ))

    # Calculate stats
    flow_sessions = [s for s in sessions if s.state == "flow"]
    total_flow = sum(s.duration_minutes for s in flow_sessions)
    avg_flow = total_flow // max(len(flow_sessions), 1)

    # Best hour/day analysis
    hour_commit_counts = {}
    day_commit_counts = {}
    for key, commits in hour_blocks.items():
        try:
            ts = datetime.fromisoformat(key.replace(" ", "T") + ":00")
            h = ts.hour
            d = ts.weekday()
            hour_commit_counts[h] = hour_commit_counts.get(h, 0) + len(commits)
            day_commit_counts[d] = day_commit_counts.get(d, 0) + len(commits)
        except ValueError:
            continue

    best_hour = max(hour_commit_counts, key=hour_commit_counts.get) if hour_commit_counts else 0
    best_day = max(day_commit_counts, key=day_commit_counts.get) if day_commit_counts else 0

    total_minutes = days * 8 * 60  # Assume 8-hour workday
    flow_ratio = total_flow / max(total_minutes, 1)

    return FlowHistory(
        sessions=sessions,
        total_flow_minutes=total_flow,
        average_flow_duration=avg_flow,
        best_flow_hour=best_hour,
        best_flow_day=best_day,
        flow_ratio=round(min(flow_ratio, 1.0), 2),
    )


# === Formatters ===

STATE_LABELS = {
    "deep_focus": "Deep Focus",
    "flow": "In Flow",
    "warming_up": "Warming Up",
    "winding_down": "Winding Down",
    "struggling": "Struggling",
    "idle": "Idle",
}

STATE_INDICATORS = {
    "deep_focus": "[####]",
    "flow": "[### ]",
    "warming_up": "[##  ]",
    "winding_down": "[#   ]",
    "struggling": "[/\\/\\]",
    "idle": "[    ]",
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def format_status_text(state: FlowState) -> str:
    """Format flow status as plain text."""
    label = STATE_LABELS.get(state.state, state.state)
    indicator = STATE_INDICATORS.get(state.state, "[    ]")

    lines = [
        "Developer Flow Status",
        "=" * 40,
        "",
        f"  State: {indicator} {label} ({state.confidence:.0%} confidence)",
        f"  Velocity: {state.commit_velocity:.1f} commits/hour",
        f"  Focus: {state.file_focus:.0%}",
        f"  Energy: {state.session_energy:.0%}",
        "",
    ]
    if state.signals:
        lines.append("  Signals:")
        for s in state.signals:
            lines.append(f"    - {s}")
        lines.append("")
    if state.suggestion:
        lines.append(f"  Suggestion: {state.suggestion}")
    return "\n".join(lines)


def format_status_slack(state: FlowState) -> str:
    """Format flow status as Slack mrkdwn."""
    label = STATE_LABELS.get(state.state, state.state)
    indicator = STATE_INDICATORS.get(state.state, "[    ]")

    lines = [
        "*Developer Flow Status*",
        "",
        f"  `{indicator}` *{label}* ({state.confidence:.0%})",
        f"  _Velocity:_ {state.commit_velocity:.1f} commits/hr | _Focus:_ {state.file_focus:.0%} | _Energy:_ {state.session_energy:.0%}",
    ]
    if state.signals:
        lines.append("")
        for s in state.signals:
            lines.append(f"  - {s}")
    if state.suggestion:
        lines.append("")
        lines.append(f"  _Tip:_ {state.suggestion}")
    return "\n".join(lines)


def format_status_json(state: FlowState) -> str:
    """Format flow status as JSON."""
    data = {
        "state": state.state,
        "label": STATE_LABELS.get(state.state, state.state),
        "confidence": state.confidence,
        "commit_velocity": state.commit_velocity,
        "file_focus": state.file_focus,
        "session_energy": state.session_energy,
        "signals": state.signals,
        "suggestion": state.suggestion,
    }
    return json.dumps(data, indent=2)


def format_history_text(history: FlowHistory) -> str:
    """Format flow history as plain text."""
    lines = [
        "Flow History",
        "=" * 40,
        "",
        f"  Total flow time: {history.total_flow_minutes} minutes",
        f"  Average flow session: {history.average_flow_duration} minutes",
        f"  Flow ratio: {history.flow_ratio:.0%}",
        f"  Best hour: {history.best_flow_hour}:00",
        f"  Best day: {DAY_NAMES[history.best_flow_day]}",
        "",
        f"  Sessions ({len(history.sessions)} total):",
    ]
    for s in history.sessions[-10:]:  # Last 10
        lines.append(f"    {s.start[:16]} - {s.duration_minutes}min, {s.commits} commits ({s.state})")
    return "\n".join(lines)


def format_tips_text(tips: list) -> str:
    """Format tips as plain text."""
    lines = ["Flow Optimization Tips", "=" * 40, ""]
    for i, tip in enumerate(tips, 1):
        lines.append(f"  {i}. {tip}")
    return "\n".join(lines)


# === CLI ===

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        fmt = sys.argv[2] if len(sys.argv) > 2 else "text"
        state = detect_flow_state()
        formatters = {"text": format_status_text, "slack": format_status_slack, "json": format_status_json}
        print(formatters.get(fmt, format_status_text)(state))

    elif cmd == "history":
        days = 7
        for i, arg in enumerate(sys.argv):
            if arg == "--days" and i + 1 < len(sys.argv):
                days = int(sys.argv[i + 1])
        history = analyze_flow_history(days)
        print(format_history_text(history))

    elif cmd == "tips":
        state = detect_flow_state()
        tips = get_flow_tips(state)
        print(format_tips_text(tips))

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: status [text|slack|json], history [--days N], tips")
        sys.exit(1)


if __name__ == "__main__":
    main()
