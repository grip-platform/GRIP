"""
Copilot Routine Predictor for GRIP.

Learns user patterns from session history and git timestamps to:
- Suggest optimal workflow timing
- Predict next likely actions
- Recommend break intervals
- Surface recurring habits

Usage:
    # From git log
    python3 lib/routine_predictor.py --source git --hours 168 --format text

    # From JSONL session data
    python3 lib/routine_predictor.py --source jsonl --format slack

    # JSON output for MCP apps
    python3 lib/routine_predictor.py --source git --format json

Version: 1.0.0
"""

import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

ACTIVITY_KEYWORDS = {
    "coding": ["feat:", "feature", "add:", "implement", "build", "create", "Write", "Edit"],
    "reviewing": ["review", "pr", "pull request", "merge", "approve", "Ralph"],
    "planning": ["plan", "issue", "milestone", "roadmap", "sprint", "backlog"],
    "debugging": ["fix:", "bug", "debug", "error", "trace", "hotfix", "patch"],
    "refactoring": ["refactor:", "cleanup", "extract", "rename", "move", "restructure"],
    "testing": ["test:", "test_", "pytest", "assert", "coverage", "spec"],
    "documenting": ["docs:", "readme", "wiki", "comment", "docstring", "changelog"],
}

HEATMAP_BLOCKS = [" ", "\u2591", "\u2592", "\u2593", "\u2588"]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SessionPattern:
    hour_of_day: int  # 0-23
    day_of_week: int  # 0=Mon, 6=Sun
    duration_minutes: int
    primary_activity: str  # "coding", "reviewing", "planning", "debugging"
    productivity_level: str  # "high", "medium", "low"
    tools_used: list = field(default_factory=list)


@dataclass
class Prediction:
    action: str  # predicted next action
    confidence: float  # 0.0-1.0
    reasoning: str
    suggested_time: str  # "now", "in 30 min", "tomorrow 9am"


@dataclass
class RoutineInsight:
    pattern_type: str  # "peak_hours", "break_needed", "optimal_task", "habit"
    description: str
    evidence: str  # what data supports this
    actionable: str  # what to do about it


@dataclass
class RoutineReport:
    patterns: list  # list[SessionPattern]
    predictions: list  # list[Prediction]
    insights: list  # list[RoutineInsight]
    peak_hours: list  # list[int] hours with highest productivity
    low_hours: list  # list[int] hours with lowest productivity
    preferred_day: str  # most productive day of week
    avg_session_duration: int  # minutes
    break_suggestion: str  # "Take a break in ~45 min"
    weekly_rhythm: dict  # day -> avg productivity
    recommendations: list  # list[str]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _classify_activity(text: str) -> str:
    """Classify text into an activity category based on keyword matching."""
    text_lower = text.lower()
    scores = {}
    for activity, keywords in ACTIVITY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in text_lower)
        if score > 0:
            scores[activity] = score
    if not scores:
        return "coding"
    return max(scores, key=scores.get)


def _estimate_productivity(hour: int, duration: int) -> str:
    """Estimate productivity level from hour and duration heuristics."""
    # Morning/afternoon sessions with reasonable duration -> high
    if 8 <= hour <= 17 and 30 <= duration <= 180:
        return "high"
    # Very short or very late sessions -> low
    if duration < 15 or hour < 5 or hour > 23:
        return "low"
    return "medium"


def parse_session_history(jsonl_text: str) -> list:
    """Parse GRIP session JSONL data into SessionPattern list.

    Expected JSONL fields:
        - timestamp / created_at / start_time: ISO datetime
        - duration / duration_minutes: int (minutes)
        - activity / primary_activity / event: str
        - tools / tools_used: list[str]
        - productivity / productivity_level: str
    """
    patterns = []
    if not jsonl_text or not jsonl_text.strip():
        return patterns

    for line in jsonl_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        # Extract timestamp
        ts_str = (
            record.get("timestamp")
            or record.get("created_at")
            or record.get("start_time")
            or record.get("date")
        )
        if not ts_str:
            continue

        try:
            if isinstance(ts_str, (int, float)):
                dt = datetime.fromtimestamp(ts_str, tz=timezone.utc)
            else:
                ts_str = str(ts_str)
                # Handle common ISO formats
                ts_clean = ts_str.replace("Z", "+00:00")
                if "T" in ts_clean:
                    dt = datetime.fromisoformat(ts_clean)
                else:
                    # Try date-only
                    dt = datetime.strptime(ts_clean[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, OSError):
            continue

        # Duration
        duration = (
            record.get("duration_minutes")
            or record.get("duration")
            or record.get("session_duration")
            or 30  # default
        )
        try:
            duration = int(duration)
        except (ValueError, TypeError):
            duration = 30

        # Activity
        activity_raw = (
            record.get("primary_activity")
            or record.get("activity")
            or record.get("event")
            or record.get("type")
            or ""
        )
        activity = _classify_activity(str(activity_raw))

        # Tools
        tools = record.get("tools_used") or record.get("tools") or []
        if isinstance(tools, str):
            tools = [tools]

        # Productivity
        prod = (
            record.get("productivity_level")
            or record.get("productivity")
            or _estimate_productivity(dt.hour, duration)
        )
        if prod not in ("high", "medium", "low"):
            prod = _estimate_productivity(dt.hour, duration)

        patterns.append(SessionPattern(
            hour_of_day=dt.hour,
            day_of_week=dt.weekday(),
            duration_minutes=duration,
            primary_activity=activity,
            productivity_level=prod,
            tools_used=list(tools),
        ))

    return patterns


def parse_git_timestamps(log_text: str) -> list:
    """Extract SessionPattern entries from git log output.

    Handles formats:
        - git log --format="%ai %s"     (2026-02-17 10:30:00 +0200 feat: ...)
        - git log --format="%aI %s"     (2026-02-17T10:30:00+02:00 feat: ...)
        - git log --format="%H %ai %s"  (hash datetime message)
        - plain ISO dates with messages
    """
    patterns = []
    if not log_text or not log_text.strip():
        return patterns

    # Regex: optional hash, then ISO-ish datetime (with optional tz), then message
    # Matches: "2026-02-17 10:30:00 +0200 feat: something"
    # Matches: "2026-02-17T10:30:00+02:00 feat: something"
    # Matches: "abc1234567890abcdef... 2026-02-17 10:30:00 +0200 feat: something"
    date_re = re.compile(
        r"(?:[a-f0-9]{7,}\s+)?"  # optional commit hash (7+ hex chars)
        r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\s*(?:[+-]\d{2}:?\d{2})?)"  # datetime with optional tz
        r"\s+(.*)"  # message
    )

    timestamps = []
    for line in log_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        m = date_re.match(line)
        if not m:
            continue

        ts_str = m.group(1).strip()
        message = m.group(2).strip()

        try:
            # Normalize to parseable format
            ts_str = ts_str.replace("T", " ")
            # Handle "+0200" -> "+02:00"
            tz_match = re.search(r"([+-])(\d{2})(\d{2})$", ts_str)
            if tz_match:
                ts_str = ts_str[:tz_match.start()] + f"{tz_match.group(1)}{tz_match.group(2)}:{tz_match.group(3)}"

            # Parse
            if "+" in ts_str[10:] or ts_str[10:].count("-") > 0:
                dt = datetime.fromisoformat(ts_str)
            else:
                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        timestamps.append((dt, message))

    if not timestamps:
        return patterns

    # Sort by time to compute inter-commit durations
    timestamps.sort(key=lambda x: x[0])

    for i, (dt, message) in enumerate(timestamps):
        # Estimate duration: time until next commit, capped at 120 min
        if i + 1 < len(timestamps):
            delta = (timestamps[i + 1][0] - dt).total_seconds() / 60
            duration = max(5, min(int(delta), 120))
        else:
            duration = 30  # default for last commit

        activity = _classify_activity(message)
        prod = _estimate_productivity(dt.hour, duration)

        patterns.append(SessionPattern(
            hour_of_day=dt.hour,
            day_of_week=dt.weekday(),
            duration_minutes=duration,
            primary_activity=activity,
            productivity_level=prod,
            tools_used=[],
        ))

    return patterns


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def detect_peak_hours(patterns: list) -> list:
    """Find the hours with highest productivity concentration.

    Returns top hours sorted by productivity score (high=3, medium=2, low=1),
    weighted by session count. Returns up to 5 peak hours.
    """
    if not patterns:
        return []

    prod_map = {"high": 3, "medium": 2, "low": 1}
    hour_scores = defaultdict(float)
    hour_counts = Counter()

    for p in patterns:
        score = prod_map.get(p.productivity_level, 1)
        hour_scores[p.hour_of_day] += score
        hour_counts[p.hour_of_day] += 1

    # Weighted score: total_score * log(count+1) to favor hours with more data
    weighted = {}
    for hour in hour_scores:
        weighted[hour] = hour_scores[hour] * math.log(hour_counts[hour] + 1)

    if not weighted:
        return []

    sorted_hours = sorted(weighted, key=weighted.get, reverse=True)
    # Return hours above median score, up to 5
    median_score = sorted(weighted.values())[len(weighted) // 2] if weighted else 0
    peaks = [h for h in sorted_hours if weighted[h] >= median_score][:5]
    return sorted(peaks)


def _detect_low_hours(patterns: list) -> list:
    """Find the hours with lowest productivity. Returns up to 5."""
    if not patterns:
        return []

    prod_map = {"high": 3, "medium": 2, "low": 1}
    hour_scores = defaultdict(list)

    for p in patterns:
        hour_scores[p.hour_of_day].append(prod_map.get(p.productivity_level, 1))

    if not hour_scores:
        return []

    hour_avgs = {h: sum(s) / len(s) for h, s in hour_scores.items()}
    sorted_hours = sorted(hour_avgs, key=hour_avgs.get)
    # Hours with below-average productivity
    overall_avg = sum(hour_avgs.values()) / len(hour_avgs) if hour_avgs else 2
    lows = [h for h in sorted_hours if hour_avgs[h] < overall_avg][:5]
    return sorted(lows)


def detect_weekly_rhythm(patterns: list) -> dict:
    """Compute average productivity score per day of week.

    Returns dict mapping day name to float score (1.0 = low, 3.0 = high).
    """
    if not patterns:
        return {name: 0.0 for name in DAY_NAMES}

    prod_map = {"high": 3, "medium": 2, "low": 1}
    day_scores = defaultdict(list)

    for p in patterns:
        day_scores[p.day_of_week].append(prod_map.get(p.productivity_level, 1))

    result = {}
    for i, name in enumerate(DAY_NAMES):
        scores = day_scores.get(i, [])
        result[name] = round(sum(scores) / len(scores), 2) if scores else 0.0

    return result


def predict_next_action(patterns: list, current_hour: int = None) -> list:
    """Predict next actions based on historical patterns at this time.

    Looks at what activities were performed around the current hour (+/- 1)
    and ranks them by frequency and productivity.
    """
    if current_hour is None:
        current_hour = datetime.now().hour

    if not patterns:
        return [Prediction(
            action="coding",
            confidence=0.1,
            reasoning="No historical data available",
            suggested_time="now",
        )]

    # Find patterns within +/- 1 hour of current time
    nearby = [p for p in patterns if abs(p.hour_of_day - current_hour) <= 1
              or abs(p.hour_of_day - current_hour) >= 23]  # wrap around midnight

    if not nearby:
        # Fall back to all patterns
        nearby = patterns

    # Count activities weighted by productivity
    prod_map = {"high": 3, "medium": 2, "low": 1}
    activity_scores = Counter()
    activity_counts = Counter()
    for p in nearby:
        weight = prod_map.get(p.productivity_level, 1)
        activity_scores[p.primary_activity] += weight
        activity_counts[p.primary_activity] += 1

    total = sum(activity_counts.values())
    predictions = []

    for activity, score in activity_scores.most_common(3):
        count = activity_counts[activity]
        confidence = round(min(count / max(total, 1), 0.95), 2)
        avg_prod = round(score / count, 1)

        reasoning = (
            f"{count}/{total} sessions around {current_hour}:00 involved {activity} "
            f"(avg productivity: {avg_prod}/3.0)"
        )

        # Suggest timing
        if count / max(total, 1) > 0.5:
            suggested = "now"
        elif current_hour < 12:
            suggested = "this morning"
        elif current_hour < 17:
            suggested = "this afternoon"
        else:
            suggested = "tomorrow morning"

        predictions.append(Prediction(
            action=activity,
            confidence=confidence,
            reasoning=reasoning,
            suggested_time=suggested,
        ))

    return predictions if predictions else [Prediction(
        action="coding",
        confidence=0.1,
        reasoning="Insufficient data for prediction",
        suggested_time="now",
    )]


def suggest_break(session_start: str, patterns: list) -> str:
    """Suggest when to take a break based on typical session durations.

    Args:
        session_start: ISO datetime string of current session start
        patterns: historical patterns for duration reference
    """
    try:
        start_str = session_start.replace("Z", "+00:00")
        start = datetime.fromisoformat(start_str)
    except (ValueError, TypeError):
        return "Consider a break every 90 minutes"

    now = datetime.now(tz=start.tzinfo or timezone.utc)
    elapsed = (now - start).total_seconds() / 60

    if not patterns:
        avg_duration = 90
    else:
        durations = [p.duration_minutes for p in patterns if p.duration_minutes > 0]
        avg_duration = sum(durations) / len(durations) if durations else 90

    remaining = max(0, avg_duration - elapsed)

    if elapsed > avg_duration * 1.5:
        return "You've exceeded your typical session length by 50%. Take a break now."
    elif elapsed > avg_duration:
        return "You've passed your typical session duration. A break would help."
    elif remaining < 15:
        return f"Take a break in ~{int(remaining)} min (typical session: {int(avg_duration)} min)"
    elif remaining < 30:
        return f"Break coming up in ~{int(remaining)} min"
    else:
        return f"Take a break in ~{int(remaining)} min (typical session: {int(avg_duration)} min)"


def _detect_hour_habits(patterns: list) -> list:
    """Detect hour-activity correlations: dominant activity per hour >= 60%."""
    insights = []
    hour_activities = defaultdict(list)
    for p in patterns:
        hour_activities[p.hour_of_day].append(p.primary_activity)

    for hour, activities in hour_activities.items():
        if len(activities) < 3:
            continue
        counter = Counter(activities)
        top_activity, top_count = counter.most_common(1)[0]
        ratio = top_count / len(activities)
        if ratio >= 0.6:
            insights.append(RoutineInsight(
                pattern_type="habit",
                description=f"You tend to do {top_activity} at {hour}:00",
                evidence=f"{top_count}/{len(activities)} sessions ({ratio:.0%}) at {hour}:00 are {top_activity}",
                actionable=f"Schedule {top_activity} tasks around {hour}:00 for natural alignment",
            ))
    return insights


def _detect_day_habits(patterns: list) -> list:
    """Detect day-of-week preferences: dominant activity per day >= 60%."""
    insights = []
    day_activities = defaultdict(list)
    for p in patterns:
        day_activities[p.day_of_week].append(p.primary_activity)

    for day_idx, activities in day_activities.items():
        if len(activities) < 3:
            continue
        counter = Counter(activities)
        top_activity, top_count = counter.most_common(1)[0]
        ratio = top_count / len(activities)
        if ratio >= 0.6:
            day_name = DAY_NAMES[day_idx]
            insights.append(RoutineInsight(
                pattern_type="habit",
                description=f"{day_name}s are your {top_activity} day",
                evidence=f"{top_count}/{len(activities)} sessions ({ratio:.0%}) on {day_name} are {top_activity}",
                actionable=f"Lean into {day_name} as your {top_activity} day",
            ))
    return insights


def _detect_productivity_blocks(patterns: list) -> list:
    """Classify morning/afternoon/evening productivity levels."""
    insights = []
    prod_map = {"high": 3, "medium": 2, "low": 1}
    morning = [p for p in patterns if 6 <= p.hour_of_day < 12]
    afternoon = [p for p in patterns if 12 <= p.hour_of_day < 18]
    evening = [p for p in patterns if 18 <= p.hour_of_day < 24]

    for block_name, block in [("morning", morning), ("afternoon", afternoon), ("evening", evening)]:
        if len(block) < 3:
            continue
        avg_prod = sum(prod_map.get(p.productivity_level, 1) for p in block) / len(block)
        if avg_prod >= 2.5:
            insights.append(RoutineInsight(
                pattern_type="peak_hours",
                description=f"Your {block_name} sessions are highly productive",
                evidence=f"Average productivity: {avg_prod:.1f}/3.0 across {len(block)} sessions",
                actionable=f"Protect {block_name} time for deep work",
            ))
        elif avg_prod < 1.5:
            insights.append(RoutineInsight(
                pattern_type="optimal_task",
                description=f"Your {block_name} sessions have lower productivity",
                evidence=f"Average productivity: {avg_prod:.1f}/3.0 across {len(block)} sessions",
                actionable=f"Use {block_name} for lighter tasks (reviews, planning)",
            ))
    return insights


def _detect_duration_patterns(patterns: list) -> list:
    """Detect marathon session patterns from duration data."""
    insights = []
    durations = [p.duration_minutes for p in patterns]
    avg_dur = sum(durations) / len(durations)
    long_sessions = [d for d in durations if d > avg_dur * 1.5]
    if len(long_sessions) > len(durations) * 0.3:
        insights.append(RoutineInsight(
            pattern_type="break_needed",
            description="You frequently have marathon sessions",
            evidence=f"{len(long_sessions)}/{len(durations)} sessions exceed {int(avg_dur * 1.5)} min",
            actionable=f"Set a timer at {int(avg_dur)} min to prompt breaks",
        ))
    elif avg_dur > 120:
        insights.append(RoutineInsight(
            pattern_type="break_needed",
            description="You frequently have marathon sessions",
            evidence=f"Average session duration is {int(avg_dur)} min (>{120} min threshold)",
            actionable=f"Set a timer at 90 min to prompt breaks",
        ))
    return insights


def _detect_tool_consistency(patterns: list) -> list:
    """Detect top tool usage across sessions."""
    insights = []
    all_tools = []
    for p in patterns:
        all_tools.extend(p.tools_used)
    if all_tools:
        tool_counts = Counter(all_tools)
        top_tools = tool_counts.most_common(3)
        if top_tools and top_tools[0][1] > len(patterns) * 0.5:
            tool_name = top_tools[0][0]
            insights.append(RoutineInsight(
                pattern_type="habit",
                description=f"Your most-used tool is {tool_name}",
                evidence=f"Used in {top_tools[0][1]}/{len(patterns)} sessions",
                actionable=f"Consider optimizing your {tool_name} workflow",
            ))
    return insights


def detect_habits(patterns: list) -> list:
    """Detect recurring patterns and habits from session history."""
    if not patterns or len(patterns) < 3:
        return []
    insights = []
    insights.extend(_detect_hour_habits(patterns))
    insights.extend(_detect_day_habits(patterns))
    insights.extend(_detect_productivity_blocks(patterns))
    insights.extend(_detect_duration_patterns(patterns))
    insights.extend(_detect_tool_consistency(patterns))
    return insights


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------

def analyze_routine(patterns: list, current_hour: int = None) -> RoutineReport:
    """Run full routine analysis and return a comprehensive report."""
    if current_hour is None:
        current_hour = datetime.now().hour

    peak_hours = detect_peak_hours(patterns)
    low_hours = _detect_low_hours(patterns)
    weekly = detect_weekly_rhythm(patterns)
    predictions = predict_next_action(patterns, current_hour)
    habits = detect_habits(patterns)

    # Preferred day
    active_days = {k: v for k, v in weekly.items() if v > 0}
    preferred_day = max(active_days, key=active_days.get) if active_days else "N/A"

    # Average session duration
    durations = [p.duration_minutes for p in patterns] if patterns else []
    avg_duration = int(sum(durations) / len(durations)) if durations else 0

    # Break suggestion based on average
    if avg_duration > 0:
        break_msg = f"Take a break every ~{avg_duration} min (your typical session length)"
    else:
        break_msg = "No session data available for break recommendations"

    report = RoutineReport(
        patterns=patterns,
        predictions=predictions,
        insights=habits,
        peak_hours=peak_hours,
        low_hours=low_hours,
        preferred_day=preferred_day,
        avg_session_duration=avg_duration,
        break_suggestion=break_msg,
        weekly_rhythm=weekly,
        recommendations=[],
    )

    report.recommendations = generate_recommendations(report)
    return report


def _rec_peak_hours(report) -> list:
    """Recommend scheduling deep work during peak hours."""
    if report.peak_hours:
        hours_str = ", ".join(f"{h}:00" for h in report.peak_hours)
        return [f"Schedule deep work during your peak hours: {hours_str}"]
    return []


def _rec_low_hours(report) -> list:
    """Recommend routine tasks during low-productivity hours."""
    if report.low_hours:
        hours_str = ", ".join(f"{h}:00" for h in report.low_hours)
        return [f"Use low-productivity hours ({hours_str}) for routine tasks like reviews and admin"]
    return []


def _rec_session_duration(report) -> list:
    """Recommend session duration adjustments."""
    if report.avg_session_duration > 120:
        return [
            f"Your average session is {report.avg_session_duration} min. "
            "Consider shorter, focused sessions with breaks."
        ]
    elif report.avg_session_duration < 20 and report.avg_session_duration > 0:
        return [
            f"Your sessions average only {report.avg_session_duration} min. "
            "Try longer focused blocks for complex tasks."
        ]
    return []


def _rec_weekly_rhythm(report) -> list:
    """Recommend weekly rhythm adjustments."""
    active_days = {k: v for k, v in report.weekly_rhythm.items() if v > 0}
    if active_days:
        if len(active_days) >= 7:
            return ["You work every day of the week. Consider designating rest days."]
        elif len(active_days) <= 2:
            return [
                "You concentrate work into few days. "
                "Spreading sessions could reduce fatigue."
            ]
    return []


def _rec_activity_diversity(report) -> list:
    """Recommend activity diversification when one type dominates."""
    if not report.patterns:
        return []
    activities = Counter(p.primary_activity for p in report.patterns)
    if len(activities) == 1:
        only = list(activities.keys())[0]
        return [
            f"All sessions are {only}. "
            "Mix in planning and review for better outcomes."
        ]
    elif activities.most_common(1)[0][1] > len(report.patterns) * 0.8:
        dominant = activities.most_common(1)[0][0]
        return [
            f"{dominant.capitalize()} dominates {activities.most_common(1)[0][1]}/{len(report.patterns)} sessions. "
            "Balance with other activities."
        ]
    return []


def _rec_burnout_signals(report) -> list:
    """Flag burnout risk from marathon sessions."""
    marathon = sum(1 for p in report.patterns if p.duration_minutes > 180)
    if marathon > len(report.patterns) * 0.2:
        return [
            f"{marathon} marathon sessions (>3h) detected. "
            "Extended sessions reduce code quality. Take breaks."
        ]
    return []


def _rec_late_night(report) -> list:
    """Flag late-night coding sessions."""
    late = sum(1 for p in report.patterns if p.hour_of_day >= 22 or p.hour_of_day < 5)
    if late > len(report.patterns) * 0.15:
        return [
            f"{late} late-night sessions detected. "
            "Night coding correlates with higher bug rates."
        ]
    return []


def _rec_predictions(report) -> list:
    """Generate prediction-based recommendations."""
    recs = []
    for pred in report.predictions[:1]:
        if pred.confidence > 0.6:
            recs.append(
                f"Based on your routine, you'll likely do {pred.action} next "
                f"({pred.confidence:.0%} confidence)"
            )
    return recs


def generate_recommendations(report: RoutineReport) -> list:
    """Generate actionable workflow improvement recommendations."""
    if not report.patterns:
        return ["Start tracking sessions to build routine insights"]
    recs = []
    recs.extend(_rec_peak_hours(report))
    recs.extend(_rec_low_hours(report))
    recs.extend(_rec_session_duration(report))
    recs.extend(_rec_weekly_rhythm(report))
    recs.extend(_rec_activity_diversity(report))
    recs.extend(_rec_burnout_signals(report))
    recs.extend(_rec_late_night(report))
    recs.extend(_rec_predictions(report))
    if not recs:
        recs.append("Keep building your session history for better routine insights")
    return recs


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _hour_heatmap(patterns: list) -> str:
    """Generate a 24-hour heatmap using block characters."""
    if not patterns:
        return "  No data for heatmap"

    counts = Counter(p.hour_of_day for p in patterns)
    max_count = max(counts.values()) if counts else 1

    lines = []
    lines.append("  Hour  Activity")
    lines.append("  " + "-" * 40)

    for hour in range(24):
        count = counts.get(hour, 0)
        if max_count > 0:
            level = min(4, int((count / max_count) * 4))
        else:
            level = 0
        bar = HEATMAP_BLOCKS[level] * min(count, 20) if count > 0 else ""
        label = f"  {hour:02d}:00 "
        lines.append(f"{label}{bar} ({count})")

    return "\n".join(lines)


def format_text(report: RoutineReport) -> str:
    """Format report for terminal display with heatmap."""
    lines = []
    lines.append("=" * 50)
    lines.append("  ROUTINE ANALYSIS")
    lines.append("=" * 50)
    lines.append("")

    # Summary
    lines.append(f"  Sessions analyzed: {len(report.patterns)}")
    lines.append(f"  Avg session: {report.avg_session_duration} min")
    lines.append(f"  Preferred day: {report.preferred_day}")
    if report.peak_hours:
        lines.append(f"  Peak hours: {', '.join(f'{h}:00' for h in report.peak_hours)}")
    if report.low_hours:
        lines.append(f"  Low hours: {', '.join(f'{h}:00' for h in report.low_hours)}")
    lines.append("")

    # Heatmap
    lines.append("  HOUR-OF-DAY HEATMAP")
    lines.append(_hour_heatmap(report.patterns))
    lines.append("")

    # Weekly rhythm
    lines.append("  WEEKLY RHYTHM")
    for day, score in report.weekly_rhythm.items():
        bar = HEATMAP_BLOCKS[min(4, int(score))] * int(score * 3) if score > 0 else "-"
        lines.append(f"  {day:>9s}: {bar} ({score:.1f})")
    lines.append("")

    # Predictions
    if report.predictions:
        lines.append("  PREDICTIONS")
        for pred in report.predictions:
            lines.append(f"  - {pred.action} ({pred.confidence:.0%}) -> {pred.suggested_time}")
            lines.append(f"    {pred.reasoning}")
        lines.append("")

    # Insights
    if report.insights:
        lines.append("  INSIGHTS")
        for ins in report.insights:
            lines.append(f"  [{ins.pattern_type}] {ins.description}")
            lines.append(f"    Evidence: {ins.evidence}")
            lines.append(f"    Action: {ins.actionable}")
        lines.append("")

    # Break suggestion
    lines.append(f"  BREAK: {report.break_suggestion}")
    lines.append("")

    # Recommendations
    if report.recommendations:
        lines.append("  RECOMMENDATIONS")
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"  {i}. {rec}")

    lines.append("")
    lines.append("=" * 50)
    return "\n".join(lines)


def format_slack(report: RoutineReport) -> str:
    """Format report for Slack using mrkdwn (*bold* not **bold**)."""
    lines = []
    lines.append("*Routine Analysis*")
    lines.append("")

    lines.append(f"*Sessions*: {len(report.patterns)} | *Avg duration*: {report.avg_session_duration} min | *Best day*: {report.preferred_day}")

    if report.peak_hours:
        lines.append(f"*Peak hours*: {', '.join(f'{h}:00' for h in report.peak_hours)}")
    if report.low_hours:
        lines.append(f"_Low hours_: {', '.join(f'{h}:00' for h in report.low_hours)}")
    lines.append("")

    # Weekly rhythm
    lines.append("*Weekly Rhythm*")
    for day, score in report.weekly_rhythm.items():
        if score > 0:
            bar = "\u2588" * int(score * 2)
            lines.append(f"`{day:>9s}` {bar} {score:.1f}")
    lines.append("")

    # Predictions
    if report.predictions:
        lines.append("*Predictions*")
        for pred in report.predictions:
            lines.append(f"\u2022 *{pred.action}* ({pred.confidence:.0%}) \u2192 _{pred.suggested_time}_")
        lines.append("")

    # Insights
    if report.insights:
        lines.append("*Insights*")
        for ins in report.insights:
            lines.append(f"\u2022 {ins.description}")
            lines.append(f"  _{ins.actionable}_")
        lines.append("")

    # Break
    lines.append(f"*Break*: {report.break_suggestion}")
    lines.append("")

    # Recommendations
    if report.recommendations:
        lines.append("*Recommendations*")
        for rec in report.recommendations:
            lines.append(f"\u2022 {rec}")

    return "\n".join(lines)


def format_json(report: RoutineReport) -> str:
    """Format report as JSON string."""
    data = {
        "session_count": len(report.patterns),
        "avg_session_duration": report.avg_session_duration,
        "preferred_day": report.preferred_day,
        "peak_hours": report.peak_hours,
        "low_hours": report.low_hours,
        "weekly_rhythm": report.weekly_rhythm,
        "break_suggestion": report.break_suggestion,
        "predictions": [asdict(p) for p in report.predictions],
        "insights": [asdict(i) for i in report.insights],
        "recommendations": report.recommendations,
        "patterns_summary": {
            "activities": dict(Counter(p.primary_activity for p in report.patterns)) if report.patterns else {},
            "productivity": dict(Counter(p.productivity_level for p in report.patterns)) if report.patterns else {},
        },
    }
    return json.dumps(data, indent=2)


def format_timeline(report: RoutineReport) -> dict:
    """Return dict for grip_timeline MCP app (daily routine visualization)."""
    events = []
    hour_activities = defaultdict(list)

    for p in report.patterns:
        hour_activities[p.hour_of_day].append(p.primary_activity)

    # Build timeline events from most common activities per hour
    for hour in range(24):
        activities = hour_activities.get(hour, [])
        if not activities:
            continue
        top = Counter(activities).most_common(1)[0]
        is_peak = hour in report.peak_hours
        events.append({
            "hour": hour,
            "label": f"{hour:02d}:00",
            "activity": top[0],
            "count": top[1],
            "is_peak": is_peak,
            "intensity": min(1.0, top[1] / max(len(report.patterns) * 0.1, 1)),
        })

    return {
        "type": "routine_timeline",
        "title": "Daily Routine",
        "events": events,
        "peak_hours": report.peak_hours,
        "low_hours": report.low_hours,
        "preferred_day": report.preferred_day,
        "avg_session_minutes": report.avg_session_duration,
    }


def format_digest(report: RoutineReport) -> dict:
    """Return dict for grip_digest MCP app (insight cards)."""
    cards = []

    # Summary card
    cards.append({
        "type": "summary",
        "title": "Session Overview",
        "content": (
            f"{len(report.patterns)} sessions analyzed. "
            f"Average duration: {report.avg_session_duration} min. "
            f"Most productive day: {report.preferred_day}."
        ),
        "priority": "high",
    })

    # Peak hours card
    if report.peak_hours:
        cards.append({
            "type": "peak_hours",
            "title": "Peak Productivity",
            "content": f"Your best hours: {', '.join(f'{h}:00' for h in report.peak_hours)}",
            "priority": "high",
        })

    # Insight cards
    for insight in report.insights:
        cards.append({
            "type": insight.pattern_type,
            "title": insight.description,
            "content": insight.evidence,
            "action": insight.actionable,
            "priority": "medium",
        })

    # Break card
    cards.append({
        "type": "break",
        "title": "Break Reminder",
        "content": report.break_suggestion,
        "priority": "low",
    })

    # Recommendation cards
    for rec in report.recommendations[:3]:
        cards.append({
            "type": "recommendation",
            "title": "Recommendation",
            "content": rec,
            "priority": "medium",
        })

    return {
        "type": "routine_digest",
        "title": "Routine Insights",
        "cards": cards,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_git_patterns(hours: int) -> list:
    """Load patterns from git log."""
    import subprocess
    since = f"--since={hours} hours ago"
    try:
        result = subprocess.run(
            ["git", "log", "--format=%ai %s", since],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return parse_git_timestamps(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


def _load_jsonl_patterns() -> list:
    """Load patterns from GRIP metrics JSONL."""
    metrics_path = CLAUDE_DIR / "learning" / "metrics.jsonl"
    if metrics_path.exists():
        return parse_session_history(metrics_path.read_text())
    return []


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="GRIP Routine Predictor")
    parser.add_argument("--source", choices=["git", "jsonl", "both"], default="both",
                        help="Data source (default: both)")
    parser.add_argument("--hours", type=int, default=168,
                        help="Hours of git history to analyze (default: 168 = 1 week)")
    parser.add_argument("--format", choices=["text", "slack", "json", "timeline", "digest"],
                        default="text", help="Output format (default: text)")
    parser.add_argument("--hour", type=int, default=None,
                        help="Current hour override for predictions (0-23)")

    args = parser.parse_args()

    patterns = []
    if args.source in ("git", "both"):
        patterns.extend(_load_git_patterns(args.hours))
    if args.source in ("jsonl", "both"):
        patterns.extend(_load_jsonl_patterns())

    if not patterns:
        print("No session data found. Use --source git or --source jsonl.", file=sys.stderr)
        sys.exit(1)

    report = analyze_routine(patterns, current_hour=args.hour)

    formatters = {
        "text": format_text,
        "slack": format_slack,
        "json": format_json,
        "timeline": lambda r: json.dumps(format_timeline(r), indent=2),
        "digest": lambda r: json.dumps(format_digest(r), indent=2),
    }

    output = formatters[args.format](report)
    print(output)


if __name__ == "__main__":
    main()
