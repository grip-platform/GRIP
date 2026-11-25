"""
Developer Focus/Flow State Tracker for GRIP.

Analyzes git commit patterns to detect flow states, suggest breaks,
calculate focus scores, and identify optimal working hours.

Usage:
    python3 lib/focus_tracker.py --days 7 --format text
    python3 lib/focus_tracker.py --format slack
    python3 lib/focus_tracker.py break

    # Programmatic
    from focus_tracker import analyze_focus, format_text
    report = analyze_focus(days=7)
    print(format_text(report))
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

# Flow detection thresholds
FLOW_COMMIT_GAP_MAX = timedelta(minutes=20)   # Max gap between commits in a flow session
WARMUP_DURATION = timedelta(minutes=10)        # Initial warm-up period
COOLDOWN_GAP = timedelta(minutes=15)           # Gap that signals cooling down
MIN_SESSION_COMMITS = 2                         # Minimum commits for a valid session
BREAK_AFTER = timedelta(minutes=90)            # Suggest break after this duration
DISTRACTION_THRESHOLD = 3                       # File-type switches in 10 min = distracted
TINY_COMMIT_LINES = 5                          # Commits with fewer lines are "tiny"


# --- Data Model ---

@dataclass
class FlowEvent:
    """A single git activity event."""
    timestamp: datetime
    event_type: str  # "commit", "file_change"
    metadata: dict = field(default_factory=dict)


@dataclass
class FocusSession:
    """A detected focus session."""
    start: datetime
    end: datetime
    commits: int
    focus_score: float
    flow_state: str  # "focused", "scattered", "warming_up", "cooling_down"
    lines_changed: int = 0
    file_types: list = field(default_factory=list)
    duration_minutes: float = 0.0


@dataclass
class StreakInfo:
    """Flow streak tracking."""
    current_streak: int = 0
    longest_streak: int = 0
    total_focused_sessions: int = 0
    total_sessions: int = 0


@dataclass
class FocusReport:
    """Complete focus analysis report."""
    sessions: list = field(default_factory=list)
    daily_pattern: dict = field(default_factory=dict)
    optimal_hours: list = field(default_factory=list)
    streak_info: StreakInfo = field(default_factory=StreakInfo)
    recommendations: list = field(default_factory=list)
    distraction_events: list = field(default_factory=list)
    break_suggestion: Optional[str] = None
    overall_score: float = 0.0
    analysis_days: int = 1
    total_commits: int = 0


# --- Git Interaction ---

def _default_git(args: list) -> str:
    """Default git command runner."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout


def _flush_commit(events, commit, ts, msg, added, deleted, types) -> None:
    """Append a FlowEvent for the accumulated commit state. Sprint 1b #8 DRY fix."""
    if not (commit and ts):
        return
    events.append(FlowEvent(
        timestamp=ts,
        event_type="commit",
        metadata={
            "hash": commit,
            "lines_added": added,
            "lines_deleted": deleted,
            "lines_total": added + deleted,
            "file_types": sorted(types),
            "message": msg,
        },
    ))


def parse_git_activity(days: int = 1, _git=None) -> list:
    """Parse git log for activity data.

    Args:
        days: Number of days to look back.
        _git: Injectable git command runner. Signature: (args: list) -> str.

    Returns:
        List of FlowEvent sorted by timestamp ascending.
    """
    git = _git or _default_git
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

    log_output = git([
        "log", "--all", f"--since={since}",
        "--format=COMMIT|%H|%aI|%s",
        "--numstat",
    ])

    events = []
    current_commit = None
    current_ts = None
    current_msg = ""
    lines_added = 0
    lines_deleted = 0
    file_types = set()

    for line in log_output.splitlines():
        line = line.strip()
        if not line:
            _flush_commit(events, current_commit, current_ts, current_msg,
                          lines_added, lines_deleted, file_types)
            current_commit = None
            lines_added = 0
            lines_deleted = 0
            file_types = set()
            continue

        if line.startswith("COMMIT|"):
            _flush_commit(events, current_commit, current_ts, current_msg,
                          lines_added, lines_deleted, file_types)
            if current_commit and current_ts:
                lines_added = 0
                lines_deleted = 0
                file_types = set()

            parts = line.split("|", 3)
            if len(parts) >= 4:
                current_commit = parts[1]
                try:
                    current_ts = datetime.fromisoformat(parts[2])
                    if current_ts.tzinfo is None:
                        current_ts = current_ts.replace(tzinfo=timezone.utc)
                except (ValueError, IndexError):
                    current_ts = None
                    current_commit = None
                    continue
                current_msg = parts[3]
            continue

        parts = line.split("\t")
        if len(parts) >= 3:
            try:
                added = int(parts[0]) if parts[0] != "-" else 0
                deleted = int(parts[1]) if parts[1] != "-" else 0
                lines_added += added
                lines_deleted += deleted
            except ValueError:
                pass
            filename = parts[2]
            ext = Path(filename).suffix.lower()
            if ext:
                file_types.add(ext)

    _flush_commit(events, current_commit, current_ts, current_msg,
                  lines_added, lines_deleted, file_types)

    events.sort(key=lambda e: e.timestamp)
    return events


# --- Flow Detection ---

def detect_flow_sessions(events: list) -> list:
    """Group events into flow sessions based on commit timing gaps.

    A new session starts when the gap between commits exceeds FLOW_COMMIT_GAP_MAX.

    Args:
        events: List of FlowEvent sorted by timestamp.

    Returns:
        List of FocusSession.
    """
    if not events:
        return []

    commit_events = [e for e in events if e.event_type == "commit"]
    if not commit_events:
        return []

    sessions = []
    session_events = [commit_events[0]]

    for i in range(1, len(commit_events)):
        gap = commit_events[i].timestamp - commit_events[i - 1].timestamp
        if gap > FLOW_COMMIT_GAP_MAX:
            # Close current session, start new one
            sessions.append(_build_session(session_events))
            session_events = [commit_events[i]]
        else:
            session_events.append(commit_events[i])

    # Close final session
    if session_events:
        sessions.append(_build_session(session_events))

    return sessions


def _build_session(events: list) -> FocusSession:
    """Build a FocusSession from a group of events."""
    start = events[0].timestamp
    end = events[-1].timestamp
    duration = (end - start).total_seconds() / 60.0

    total_lines = sum(e.metadata.get("lines_total", 0) for e in events)
    all_types = set()
    for e in events:
        all_types.update(e.metadata.get("file_types", []))

    session = FocusSession(
        start=start,
        end=end,
        commits=len(events),
        focus_score=0.0,
        flow_state="warming_up",
        lines_changed=total_lines,
        file_types=sorted(all_types),
        duration_minutes=duration,
    )

    session.focus_score = calculate_focus_score(session, events)
    session.flow_state = _classify_flow_state(session, events)
    return session


def _classify_flow_state(session: FocusSession, events: list) -> str:
    """Classify the flow state of a session."""
    if session.commits < MIN_SESSION_COMMITS:
        return "warming_up"

    if session.duration_minutes < WARMUP_DURATION.total_seconds() / 60.0:
        return "warming_up"

    # Check for scattered pattern: many tiny commits
    tiny_commits = sum(
        1 for e in events
        if e.metadata.get("lines_total", 0) < TINY_COMMIT_LINES
    )
    if tiny_commits > len(events) * 0.6:
        return "scattered"

    # Check for cooling down: last commit gap is large
    if len(events) >= 2:
        last_gap = (events[-1].timestamp - events[-2].timestamp).total_seconds() / 60.0
        if last_gap > COOLDOWN_GAP.total_seconds() / 60.0:
            return "cooling_down"

    if session.focus_score >= 60:
        return "focused"

    return "scattered"


# --- Focus Score ---

def calculate_focus_score(session, events=None) -> float:
    """Calculate a 0-100 focus score for a session.

    Components:
    - Commit frequency consistency (0-30): Even spacing = higher
    - Lines per commit depth (0-30): More substantial changes = higher
    - File type coherence (0-20): Fewer type switches = higher
    - Duration bonus (0-20): Longer sustained sessions = higher

    Args:
        session: FocusSession (or any object with commits, lines_changed,
                 file_types, duration_minutes).
        events: Optional list of FlowEvent for gap analysis.

    Returns:
        Score from 0.0 to 100.0.
    """
    if session.commits == 0:
        return 0.0

    score = 0.0

    # 1. Commit frequency consistency (0-30)
    if events and len(events) >= 2:
        gaps = []
        for i in range(1, len(events)):
            gap = (events[i].timestamp - events[i - 1].timestamp).total_seconds()
            gaps.append(gap)
        if gaps:
            avg_gap = sum(gaps) / len(gaps)
            if avg_gap > 0:
                variance = sum((g - avg_gap) ** 2 for g in gaps) / len(gaps)
                std_dev = variance ** 0.5
                cv = std_dev / avg_gap if avg_gap else 0
                # Lower CV = more consistent = higher score
                consistency = max(0, 1 - cv) * 30
                score += consistency
            else:
                score += 15  # All simultaneous
    elif session.commits == 1:
        score += 10  # Single commit gets partial credit

    # 2. Lines per commit depth (0-30)
    avg_lines = session.lines_changed / max(session.commits, 1)
    if avg_lines >= 50:
        score += 30
    elif avg_lines >= 20:
        score += 25
    elif avg_lines >= 10:
        score += 20
    elif avg_lines >= 5:
        score += 15
    else:
        score += 5

    # 3. File type coherence (0-20)
    num_types = len(session.file_types)
    if num_types <= 1:
        score += 20
    elif num_types <= 2:
        score += 16
    elif num_types <= 3:
        score += 12
    elif num_types <= 5:
        score += 8
    else:
        score += 4

    # 4. Duration bonus (0-20)
    duration = session.duration_minutes
    if duration >= 60:
        score += 20
    elif duration >= 30:
        score += 15
    elif duration >= 15:
        score += 10
    elif duration >= 5:
        score += 5
    else:
        score += 2

    return min(100.0, max(0.0, round(score, 1)))


# --- Optimal Hours ---

def detect_optimal_hours(events: list) -> list:
    """Find peak productivity hours based on commit density and size.

    Args:
        events: List of FlowEvent.

    Returns:
        List of dicts with hour (0-23), commit_count, avg_lines, score.
        Sorted by score descending.
    """
    if not events:
        return []

    hourly = {}
    for e in events:
        if e.event_type != "commit":
            continue
        hour = e.timestamp.hour
        if hour not in hourly:
            hourly[hour] = {"commits": 0, "total_lines": 0}
        hourly[hour]["commits"] += 1
        hourly[hour]["total_lines"] += e.metadata.get("lines_total", 0)

    if not hourly:
        return []

    max_commits = max(h["commits"] for h in hourly.values())
    max_lines = max(h["total_lines"] for h in hourly.values()) or 1

    results = []
    for hour, data in hourly.items():
        commit_score = (data["commits"] / max_commits) * 50
        lines_score = (data["total_lines"] / max_lines) * 50
        score = round(commit_score + lines_score, 1)
        results.append({
            "hour": hour,
            "commit_count": data["commits"],
            "avg_lines": round(data["total_lines"] / data["commits"], 1) if data["commits"] else 0,
            "score": score,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# --- Distraction Detection ---

def detect_distractions(events: list) -> list:
    """Identify distraction patterns in commit history.

    Patterns detected:
    - rapid_type_switching: Many file-type switches in short window
    - tiny_commits: Burst of very small commits
    - erratic_timing: Highly inconsistent commit gaps

    Args:
        events: List of FlowEvent sorted by timestamp.

    Returns:
        List of distraction dicts with type, timestamp, details.
    """
    if not events:
        return []

    commit_events = [e for e in events if e.event_type == "commit"]
    if len(commit_events) < 2:
        return []

    distractions = []

    # 1. Rapid file-type switching (sliding 10-min window)
    window = timedelta(minutes=10)
    for i, event in enumerate(commit_events):
        window_events = [
            e for e in commit_events
            if timedelta(0) <= (e.timestamp - event.timestamp) <= window
        ]
        if len(window_events) >= 3:
            types_seen = set()
            for we in window_events:
                types_seen.update(we.metadata.get("file_types", []))
            if len(types_seen) >= DISTRACTION_THRESHOLD:
                distractions.append({
                    "type": "rapid_type_switching",
                    "timestamp": event.timestamp.isoformat(),
                    "details": f"{len(types_seen)} file types in {len(window_events)} commits within 10 min",
                    "file_types": sorted(types_seen),
                })

    # 2. Tiny commit bursts (3+ tiny commits in a row)
    streak = 0
    streak_start = None
    for event in commit_events:
        if event.metadata.get("lines_total", 0) < TINY_COMMIT_LINES:
            if streak == 0:
                streak_start = event.timestamp
            streak += 1
        else:
            if streak >= 3:
                distractions.append({
                    "type": "tiny_commits",
                    "timestamp": streak_start.isoformat(),
                    "details": f"{streak} consecutive commits under {TINY_COMMIT_LINES} lines",
                })
            streak = 0
    if streak >= 3 and streak_start:
        distractions.append({
            "type": "tiny_commits",
            "timestamp": streak_start.isoformat(),
            "details": f"{streak} consecutive commits under {TINY_COMMIT_LINES} lines",
        })

    # 3. Erratic timing (CV > 1.5 across session)
    if len(commit_events) >= 4:
        gaps = []
        for i in range(1, len(commit_events)):
            gap = (commit_events[i].timestamp - commit_events[i - 1].timestamp).total_seconds()
            gaps.append(gap)
        if gaps:
            avg = sum(gaps) / len(gaps)
            if avg > 0:
                variance = sum((g - avg) ** 2 for g in gaps) / len(gaps)
                cv = (variance ** 0.5) / avg
                if cv > 1.5:
                    distractions.append({
                        "type": "erratic_timing",
                        "timestamp": commit_events[0].timestamp.isoformat(),
                        "details": f"Coefficient of variation {cv:.2f} (threshold: 1.5)",
                    })

    # Deduplicate rapid_type_switching by keeping only distinct 10-min windows
    seen_windows = set()
    deduped = []
    for d in distractions:
        if d["type"] == "rapid_type_switching":
            ts = d["timestamp"][:16]  # Truncate to minute
            if ts not in seen_windows:
                seen_windows.add(ts)
                deduped.append(d)
        else:
            deduped.append(d)

    return deduped


# --- Break Suggestion ---

def suggest_break(events: list) -> Optional[str]:
    """Suggest when to take a break based on current activity.

    Logic:
    - If last commit was > BREAK_AFTER ago with sustained activity: suggest break now
    - If session duration exceeds 90 min: suggest break
    - If distraction patterns detected: suggest mindful break

    Args:
        events: List of FlowEvent sorted by timestamp.

    Returns:
        Break suggestion string or None.
    """
    if not events:
        return None

    commit_events = [e for e in events if e.event_type == "commit"]
    if not commit_events:
        return None

    now = datetime.now(timezone.utc)
    last_commit = commit_events[-1].timestamp
    # Ensure timezone-aware comparison
    if last_commit.tzinfo is None:
        last_commit = last_commit.replace(tzinfo=timezone.utc)

    first_commit = commit_events[0].timestamp
    if first_commit.tzinfo is None:
        first_commit = first_commit.replace(tzinfo=timezone.utc)

    session_duration = last_commit - first_commit
    since_last = now - last_commit

    # Long session without recent break
    if session_duration > BREAK_AFTER and since_last < timedelta(minutes=5):
        minutes = int(session_duration.total_seconds() / 60)
        return (
            f"You've been coding for {minutes} minutes straight. "
            f"Take a 10-minute break - stretch, hydrate, rest your eyes."
        )

    # Distraction pattern detected
    distractions = detect_distractions(commit_events[-10:])
    if distractions:
        types = set(d["type"] for d in distractions)
        if "rapid_type_switching" in types or "tiny_commits" in types:
            return (
                "Focus appears fragmented. Consider: step away for 5 minutes, "
                "then return with a clear single objective."
            )

    # Long gap after sustained work
    if (since_last > timedelta(minutes=30)
            and session_duration > timedelta(minutes=45)
            and len(commit_events) >= 3):
        return (
            "Good natural break point detected. If you're still working, "
            "consider a quick stretch before diving back in."
        )

    return None


# --- Flow Streaks ---

def _calculate_streaks(sessions: list) -> StreakInfo:
    """Calculate focus streaks from sessions."""
    if not sessions:
        return StreakInfo()

    focused = [s for s in sessions if s.flow_state == "focused"]
    total = len(sessions)

    # Calculate streaks (consecutive focused sessions)
    current_streak = 0
    longest_streak = 0
    temp_streak = 0

    for s in sessions:
        if s.flow_state == "focused":
            temp_streak += 1
            longest_streak = max(longest_streak, temp_streak)
        else:
            temp_streak = 0

    # Current streak = from the end
    for s in reversed(sessions):
        if s.flow_state == "focused":
            current_streak += 1
        else:
            break

    return StreakInfo(
        current_streak=current_streak,
        longest_streak=longest_streak,
        total_focused_sessions=len(focused),
        total_sessions=total,
    )


# --- Daily Pattern ---

def _build_daily_pattern(events: list) -> dict:
    """Build hourly activity pattern."""
    pattern = {}
    for e in events:
        if e.event_type != "commit":
            continue
        hour = e.timestamp.hour
        day = e.timestamp.strftime("%A")
        key = f"{day}_{hour:02d}"
        if key not in pattern:
            pattern[key] = {"day": day, "hour": hour, "commits": 0, "lines": 0}
        pattern[key]["commits"] += 1
        pattern[key]["lines"] += e.metadata.get("lines_total", 0)
    return pattern


# --- Recommendations ---

def generate_recommendations(report) -> list:
    """Generate actionable recommendations based on focus report.

    Args:
        report: FocusReport.

    Returns:
        List of recommendation strings.
    """
    recs = []

    if not report.sessions:
        recs.append("No coding sessions detected. Start committing to build your focus profile.")
        return recs

    # Score-based recommendations
    if report.overall_score >= 80:
        recs.append("Excellent focus. Maintain your current rhythm.")
    elif report.overall_score >= 60:
        recs.append("Good focus overall. Try reducing context switches to improve further.")
    elif report.overall_score >= 40:
        recs.append("Moderate focus. Consider working in dedicated 45-minute blocks with clear objectives.")
    else:
        recs.append("Focus needs improvement. Try the Pomodoro technique: 25 min focused work, 5 min break.")

    # Optimal hours
    if report.optimal_hours:
        top_hours = report.optimal_hours[:3]
        hour_strs = [f"{h['hour']:02d}:00" for h in top_hours]
        recs.append(f"Your peak hours are {', '.join(hour_strs)}. Schedule deep work during these times.")

    # Distraction patterns
    if report.distraction_events:
        types = set(d["type"] for d in report.distraction_events)
        if "rapid_type_switching" in types:
            recs.append("Frequent file-type switching detected. Group related changes into focused batches.")
        if "tiny_commits" in types:
            recs.append("Many tiny commits detected. Consider batching small changes into meaningful commits.")
        if "erratic_timing" in types:
            recs.append("Erratic commit timing suggests interrupted flow. Minimize notifications during coding.")

    # Streak encouragement
    if report.streak_info.current_streak >= 3:
        recs.append(f"You're on a {report.streak_info.current_streak}-session focus streak. Keep it going.")
    elif report.streak_info.longest_streak >= 3:
        recs.append(
            f"Your best streak was {report.streak_info.longest_streak} focused sessions. "
            f"Current: {report.streak_info.current_streak}. Build it back up."
        )

    # Session duration
    long_sessions = [s for s in report.sessions if s.duration_minutes > 120]
    if long_sessions:
        recs.append(
            f"{len(long_sessions)} sessions exceeded 2 hours. "
            "Break these into 60-90 min blocks with short breaks."
        )

    # Weekend patterns
    weekend_events = [
        e for e in report.distraction_events
        if "Saturday" in str(e) or "Sunday" in str(e)
    ]
    if len(report.sessions) > 5:
        weekend_sessions = [
            s for s in report.sessions
            if s.start.strftime("%A") in ("Saturday", "Sunday")
        ]
        if weekend_sessions:
            avg_weekend = sum(s.focus_score for s in weekend_sessions) / len(weekend_sessions)
            weekday_sessions = [
                s for s in report.sessions
                if s.start.strftime("%A") not in ("Saturday", "Sunday")
            ]
            if weekday_sessions:
                avg_weekday = sum(s.focus_score for s in weekday_sessions) / len(weekday_sessions)
                if avg_weekend < avg_weekday - 10:
                    recs.append("Weekend focus is notably lower. Consider reserving weekends for rest.")

    return recs


# --- Full Pipeline ---

def analyze_focus(days: int = 1, _git=None) -> FocusReport:
    """Full focus analysis pipeline.

    Args:
        days: Number of days to analyze.
        _git: Injectable git runner for testing.

    Returns:
        FocusReport with all analysis complete.
    """
    events = parse_git_activity(days=days, _git=_git)

    sessions = detect_flow_sessions(events)
    optimal = detect_optimal_hours(events)
    distractions = detect_distractions(events)
    break_sug = suggest_break(events)
    streaks = _calculate_streaks(sessions)
    daily = _build_daily_pattern(events)

    overall = 0.0
    if sessions:
        overall = round(sum(s.focus_score for s in sessions) / len(sessions), 1)

    report = FocusReport(
        sessions=sessions,
        daily_pattern=daily,
        optimal_hours=optimal,
        streak_info=streaks,
        distraction_events=distractions,
        break_suggestion=break_sug,
        overall_score=overall,
        analysis_days=days,
        total_commits=len([e for e in events if e.event_type == "commit"]),
    )

    report.recommendations = generate_recommendations(report)
    return report


# --- Formatters ---

def _flow_bar(score: float, width: int = 20) -> str:
    """Generate a Unicode block bar for a score (0-100)."""
    blocks = ["░", "▒", "▓", "█"]
    filled = int(score / 100 * width)
    bar = ""
    for i in range(width):
        if i < filled:
            # Gradient: lighter blocks at start, darker at end
            block_idx = min(3, int(i / width * 4))
            bar += blocks[block_idx]
        else:
            bar += "░"
    return bar


def _state_icon(state: str) -> str:
    """Get a text indicator for flow state."""
    return {
        "focused": "[FLOW]",
        "scattered": "[SCAT]",
        "warming_up": "[WARM]",
        "cooling_down": "[COOL]",
    }.get(state, "[????]")


def format_text(report: FocusReport) -> str:
    """Format report for terminal display with visual flow graph.

    Args:
        report: FocusReport.

    Returns:
        Formatted text string.
    """
    lines = []
    lines.append("=" * 60)
    lines.append("  DEVELOPER FOCUS REPORT")
    lines.append(f"  Period: {report.analysis_days} day(s) | Commits: {report.total_commits}")
    lines.append("=" * 60)

    # Overall score
    bar = _flow_bar(report.overall_score)
    lines.append(f"\n  Overall Focus Score: {report.overall_score}/100")
    lines.append(f"  {bar}")

    # Sessions
    if report.sessions:
        lines.append(f"\n  Sessions ({len(report.sessions)}):")
        lines.append("  " + "-" * 56)
        for s in report.sessions:
            state = _state_icon(s.flow_state)
            bar = _flow_bar(s.focus_score, width=10)
            time_str = s.start.strftime("%H:%M")
            duration = f"{s.duration_minutes:.0f}m"
            lines.append(
                f"  {time_str} {state} {bar} "
                f"{s.focus_score:5.1f}/100  {s.commits}c  {s.lines_changed}L  {duration}"
            )
    else:
        lines.append("\n  No sessions detected.")

    # Optimal hours
    if report.optimal_hours:
        lines.append(f"\n  Peak Hours:")
        for h in report.optimal_hours[:5]:
            bar = _flow_bar(h["score"], width=10)
            lines.append(
                f"  {h['hour']:02d}:00  {bar}  "
                f"{h['commit_count']} commits, {h['avg_lines']:.0f} avg lines"
            )

    # Streaks
    si = report.streak_info
    if si.total_sessions > 0:
        lines.append(f"\n  Streaks:")
        lines.append(f"  Current: {si.current_streak} | Longest: {si.longest_streak}")
        lines.append(
            f"  Focused: {si.total_focused_sessions}/{si.total_sessions} sessions"
        )

    # Distractions
    if report.distraction_events:
        lines.append(f"\n  Distractions ({len(report.distraction_events)}):")
        for d in report.distraction_events[:5]:
            lines.append(f"  - [{d['type']}] {d['details']}")

    # Break suggestion
    if report.break_suggestion:
        lines.append(f"\n  Break: {report.break_suggestion}")

    # Recommendations
    if report.recommendations:
        lines.append(f"\n  Recommendations:")
        for r in report.recommendations:
            lines.append(f"  - {r}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def format_slack(report: FocusReport) -> str:
    """Format report for Slack (mrkdwn format).

    Uses *bold* (single asterisk), _italic_, backtick for code.

    Args:
        report: FocusReport.

    Returns:
        Slack mrkdwn formatted string.
    """
    lines = []
    lines.append(f"*Developer Focus Report* ({report.analysis_days}d)")
    lines.append(f"Score: *{report.overall_score}/100* | Commits: {report.total_commits}")

    if report.sessions:
        focused = sum(1 for s in report.sessions if s.flow_state == "focused")
        lines.append(f"\n*Sessions*: {len(report.sessions)} total, {focused} focused")
        for s in report.sessions[:5]:
            state = s.flow_state.replace("_", " ")
            lines.append(
                f"  `{s.start.strftime('%H:%M')}` _{state}_ "
                f"- {s.focus_score:.0f}/100, {s.commits}c, {s.lines_changed}L"
            )

    if report.optimal_hours:
        top3 = ", ".join(f"`{h['hour']:02d}:00`" for h in report.optimal_hours[:3])
        lines.append(f"\n*Peak Hours*: {top3}")

    si = report.streak_info
    if si.total_sessions > 0:
        lines.append(
            f"*Streaks*: current {si.current_streak}, "
            f"longest {si.longest_streak}, "
            f"{si.total_focused_sessions}/{si.total_sessions} focused"
        )

    if report.distraction_events:
        lines.append(f"\n*Distractions*: {len(report.distraction_events)}")
        for d in report.distraction_events[:3]:
            lines.append(f"  - _{d['type']}_: {d['details']}")

    if report.break_suggestion:
        lines.append(f"\n*Break*: {report.break_suggestion}")

    if report.recommendations:
        lines.append(f"\n*Recommendations*:")
        for r in report.recommendations:
            lines.append(f"  - {r}")

    return "\n".join(lines)


def format_json(report: FocusReport) -> str:
    """Format report as JSON.

    Args:
        report: FocusReport.

    Returns:
        JSON string.
    """
    def _serialize(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, "__dataclass_fields__"):
            return asdict(obj)
        return str(obj)

    data = asdict(report)
    return json.dumps(data, default=_serialize, indent=2)


def format_progress(report: FocusReport) -> dict:
    """Format report as a progress dict for grip_progress MCP app.

    Args:
        report: FocusReport.

    Returns:
        Dict with progress data.
    """
    sessions_data = []
    for s in report.sessions:
        sessions_data.append({
            "start": s.start.isoformat(),
            "end": s.end.isoformat(),
            "score": s.focus_score,
            "state": s.flow_state,
            "commits": s.commits,
            "lines": s.lines_changed,
        })

    return {
        "type": "focus_progress",
        "overall_score": report.overall_score,
        "total_commits": report.total_commits,
        "sessions": sessions_data,
        "streak": {
            "current": report.streak_info.current_streak,
            "longest": report.streak_info.longest_streak,
            "focused_ratio": (
                report.streak_info.total_focused_sessions / max(report.streak_info.total_sessions, 1)
            ),
        },
        "break_needed": report.break_suggestion is not None,
        "break_suggestion": report.break_suggestion,
        "recommendations": report.recommendations,
    }


def format_timeline(report: FocusReport) -> dict:
    """Format report as a timeline dict for grip_timeline MCP app.

    Args:
        report: FocusReport.

    Returns:
        Dict with timeline data.
    """
    events = []
    for s in report.sessions:
        events.append({
            "start": s.start.isoformat(),
            "end": s.end.isoformat(),
            "label": f"{s.flow_state} ({s.focus_score:.0f})",
            "category": s.flow_state,
            "metadata": {
                "commits": s.commits,
                "lines_changed": s.lines_changed,
                "file_types": s.file_types,
                "score": s.focus_score,
            },
        })

    for d in report.distraction_events:
        events.append({
            "start": d["timestamp"],
            "end": d["timestamp"],
            "label": d["type"],
            "category": "distraction",
            "metadata": {"details": d["details"]},
        })

    # Sort by start time
    events.sort(key=lambda e: e["start"])

    return {
        "type": "focus_timeline",
        "period_days": report.analysis_days,
        "events": events,
        "optimal_hours": report.optimal_hours[:5] if report.optimal_hours else [],
        "daily_pattern": report.daily_pattern,
    }


# --- CLI ---

def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Developer Focus Tracker")
    parser.add_argument("command", nargs="?", default="analyze",
                        choices=["analyze", "break"],
                        help="Command to run")
    parser.add_argument("--days", type=int, default=1,
                        help="Number of days to analyze (default: 1)")
    parser.add_argument("--format", dest="fmt", default="text",
                        choices=["text", "slack", "json", "progress", "timeline"],
                        help="Output format (default: text)")

    args = parser.parse_args()

    if args.command == "break":
        events = parse_git_activity(days=1)
        suggestion = suggest_break(events)
        if suggestion:
            print(suggestion)
        else:
            print("No break needed right now. Keep going.")
        return

    report = analyze_focus(days=args.days)

    formatters = {
        "text": format_text,
        "slack": format_slack,
        "json": format_json,
        "progress": lambda r: json.dumps(format_progress(r), indent=2, default=str),
        "timeline": lambda r: json.dumps(format_timeline(r), indent=2, default=str),
    }

    output = formatters[args.fmt](report)
    print(output)


if __name__ == "__main__":
    main()
