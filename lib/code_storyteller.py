#!/usr/bin/env python3
"""Code Storyteller -- turn git history into engaging developer narratives.

Transforms dry git logs into stories with chapters, moods, and protagonists.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CommitEvent:
    hash: str
    author: str
    timestamp: datetime
    message: str
    files_changed: int = 0
    additions: int = 0
    deletions: int = 0


@dataclass
class StoryChapter:
    title: str
    timespan: str
    events: list[CommitEvent] = field(default_factory=list)
    narrative: str = ""
    mood: str = "focused"


@dataclass
class CodeStory:
    title: str
    period: str
    chapters: list[StoryChapter] = field(default_factory=list)
    protagonist: str = ""
    summary: str = ""
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Git interaction (thin wrapper for mockability)
# ---------------------------------------------------------------------------

def _run_git(args: list[str]) -> str:
    """Run a git command and return stdout. Raises on failure."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


# ---------------------------------------------------------------------------
# Event extraction
# ---------------------------------------------------------------------------

_LOG_SEP = "---COMMIT_SEP---"
_FIELD_SEP = "---FIELD_SEP---"

_GIT_LOG_FORMAT = _FIELD_SEP.join(["%H", "%an", "%aI", "%s"])


def extract_events(
    days: int = 7,
    author: Optional[str] = None,
    *,
    _git: object = None,
) -> list[CommitEvent]:
    """Extract commit events from the last *days* days.

    Parameters
    ----------
    days : int
        How far back to look.
    author : str, optional
        Filter to a single author.
    _git : callable, optional
        Replacement for ``_run_git`` (used in tests).
    """
    git = _git or _run_git
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    log_args = [
        "log",
        f"--since={since}",
        f"--pretty=format:{_LOG_SEP}{_GIT_LOG_FORMAT}",
        "--shortstat",
    ]
    if author:
        log_args.append(f"--author={author}")

    raw = git(log_args)
    return _parse_log_output(raw)


def _parse_log_output(raw: str) -> list[CommitEvent]:
    """Parse the combined pretty + shortstat output into CommitEvent list.

    Git outputs:
        ---COMMIT_SEP---HASH---FIELD_SEP---AUTHOR---FIELD_SEP---DATE---FIELD_SEP---MSG
         3 files changed, 10 insertions(+), 2 deletions(-)
        ---COMMIT_SEP---HASH2---FIELD_SEP---...

    By placing the separator at the START of each commit's pretty line, each
    chunk (after the first empty one) contains the commit metadata THEN its
    shortstat on the next line.
    """
    events: list[CommitEvent] = []
    if not raw.strip():
        return events

    # Split on our separator -- the separator sits at the START of each
    # commit's pretty output, so chunk[0] is empty/whitespace and each
    # subsequent chunk starts with the commit fields followed optionally
    # by the shortstat line.
    chunks = raw.split(_LOG_SEP)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        lines = [ln for ln in chunk.split("\n") if ln.strip()]
        if not lines:
            continue

        # First line is the pretty-formatted commit info
        pretty_line = lines[0]
        parts = pretty_line.split(_FIELD_SEP)
        if len(parts) < 4:
            continue

        commit_hash, author_name, iso_ts, message = (
            parts[0].strip(),
            parts[1].strip(),
            parts[2].strip(),
            parts[3].strip(),
        )

        try:
            timestamp = datetime.fromisoformat(iso_ts)
        except ValueError:
            continue

        files_changed = 0
        additions = 0
        deletions = 0

        # Second line (if present) is the shortstat
        if len(lines) > 1:
            stat_line = lines[1]
            files_changed, additions, deletions = _parse_shortstat(stat_line)

        events.append(
            CommitEvent(
                hash=commit_hash,
                author=author_name,
                timestamp=timestamp,
                message=message,
                files_changed=files_changed,
                additions=additions,
                deletions=deletions,
            )
        )

    return events


def _parse_shortstat(line: str) -> tuple[int, int, int]:
    """Parse a ``--shortstat`` line into (files, additions, deletions)."""
    files = adds = dels = 0
    for token in line.split(","):
        token = token.strip()
        if "file" in token:
            files = _leading_int(token)
        elif "insertion" in token:
            adds = _leading_int(token)
        elif "deletion" in token:
            dels = _leading_int(token)
    return files, adds, dels


def _leading_int(s: str) -> int:
    """Extract the first integer from a string."""
    digits = ""
    for ch in s:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits) if digits else 0


# ---------------------------------------------------------------------------
# Chapter building
# ---------------------------------------------------------------------------

_CHAPTER_GAP_HOURS = 2

_TITLE_MAP: list[tuple[list[str], str]] = [
    (["feat"], "Building New Horizons"),
    (["fix"], "Squashing Bugs"),
    (["refactor"], "Reshaping the Foundations"),
    (["test"], "Fortifying the Walls"),
    (["docs", "doc"], "Writing the Chronicles"),
]

_MOOD_NARRATIVES = {
    "energetic": [
        "The keyboard was on fire.",
        "Commits flew in like a thunderstorm -- rapid, relentless, electric.",
        "No time for coffee; the code demanded full attention.",
    ],
    "focused": [
        "Steady hands, steady progress.",
        "Each commit landed with purpose and precision.",
        "A quiet confidence filled the codebase.",
    ],
    "breakthrough": [
        "Silence... then lightning.",
        "After a long stretch of thinking, the dam broke and code poured out.",
        "The breakthrough had been brewing for a while.",
    ],
    "debugging": [
        "Something was wrong, and the hunt was on.",
        "Stack traces told half the story; intuition filled the rest.",
        "One by one, the bugs fell.",
    ],
    "contemplative": [
        "Just a handful of carefully considered changes.",
        "Quality over quantity -- every line deliberate.",
        "The repository rested, but the mind did not.",
    ],
}


def group_into_chapters(events: list[CommitEvent]) -> list[StoryChapter]:
    """Group events into narrative chapters.

    Groups are formed by detecting natural breaks (>2 h gap) within a day,
    or by splitting across calendar days.
    """
    if not events:
        return []

    # Sort oldest-first for chronological storytelling
    sorted_events = sorted(events, key=lambda e: e.timestamp)

    groups: list[list[CommitEvent]] = []
    current_group: list[CommitEvent] = [sorted_events[0]]

    for prev, cur in zip(sorted_events, sorted_events[1:]):
        gap = (cur.timestamp - prev.timestamp).total_seconds() / 3600
        different_day = cur.timestamp.date() != prev.timestamp.date()
        if gap > _CHAPTER_GAP_HOURS or different_day:
            groups.append(current_group)
            current_group = [cur]
        else:
            current_group.append(cur)

    if current_group:
        groups.append(current_group)

    return [_build_chapter(group) for group in groups]


def _build_chapter(events: list[CommitEvent]) -> StoryChapter:
    title = _chapter_title(events)
    timespan = _chapter_timespan(events)
    mood = detect_mood(events)
    narrative = _chapter_narrative(events, mood)
    return StoryChapter(
        title=title,
        timespan=timespan,
        events=events,
        narrative=narrative,
        mood=mood,
    )


def _chapter_title(events: list[CommitEvent]) -> str:
    """Derive a chapter title from the dominant commit type."""
    type_counts: Counter[str] = Counter()
    for e in events:
        prefix = e.message.split(":")[0].split("(")[0].strip().lower() if ":" in e.message else ""
        type_counts[prefix] += 1

    if not type_counts:
        return "A Day of Many Things"

    dominant = type_counts.most_common(1)[0][0]
    for prefixes, title in _TITLE_MAP:
        if dominant in prefixes:
            return title

    # If no single type dominates, check if there are multiple types
    if len(type_counts) > 1:
        return "A Day of Many Things"

    return "A Day of Many Things"


def _chapter_timespan(events: list[CommitEvent]) -> str:
    if not events:
        return ""
    start = events[0].timestamp
    end = events[-1].timestamp
    if start.date() == end.date():
        return f"{start.strftime('%b %d')} ({start.strftime('%H:%M')} - {end.strftime('%H:%M')})"
    return f"{start.strftime('%b %d')} - {end.strftime('%b %d')}"


def detect_mood(events: list[CommitEvent]) -> str:
    """Detect the mood of a set of events."""
    if not events:
        return "contemplative"

    if len(events) <= 2:
        return "contemplative"

    # Check for lots of fixes
    fix_count = sum(1 for e in events if e.message.lower().startswith("fix"))
    if fix_count > len(events) * 0.5:
        return "debugging"

    # Check for rapid commits (many within short window)
    sorted_events = sorted(events, key=lambda e: e.timestamp)
    total_span_hours = (sorted_events[-1].timestamp - sorted_events[0].timestamp).total_seconds() / 3600

    if total_span_hours == 0:
        return "energetic"

    commits_per_hour = len(events) / max(total_span_hours, 0.01)

    # Check for burst after gap (breakthrough)
    if len(events) >= 3:
        gaps = []
        for i in range(1, len(sorted_events)):
            gap_h = (sorted_events[i].timestamp - sorted_events[i - 1].timestamp).total_seconds() / 3600
            gaps.append(gap_h)
        if gaps and max(gaps) > 1.5 and commits_per_hour > 2:
            return "breakthrough"

    if commits_per_hour > 3:
        return "energetic"

    return "focused"


def _chapter_narrative(events: list[CommitEvent], mood: str) -> str:
    """Build a short narrative paragraph for a chapter."""
    import random

    mood_lines = _MOOD_NARRATIVES.get(mood, _MOOD_NARRATIVES["focused"])
    # Deterministic selection based on event count so tests are stable
    opener = mood_lines[len(events) % len(mood_lines)]

    total_adds = sum(e.additions for e in events)
    total_dels = sum(e.deletions for e in events)
    total_files = sum(e.files_changed for e in events)

    parts = [opener]

    if len(events) == 1:
        parts.append(f'A single commit -- "{events[0].message}" -- told the whole tale.')
    else:
        parts.append(f"{len(events)} commits shaped this chapter.")

    if total_adds + total_dels > 0:
        parts.append(
            f"The codebase gained {total_adds} lines and shed {total_dels}, "
            f"across {total_files} files."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Story assembly
# ---------------------------------------------------------------------------

def build_story(
    events: list[CommitEvent],
    period_label: Optional[str] = None,
) -> CodeStory:
    """Assemble a complete CodeStory from events."""
    if not events:
        return CodeStory(
            title="The Silent Repository",
            period=period_label or "unknown",
            chapters=[],
            protagonist="nobody",
            summary="Nothing happened. The repository slept peacefully.",
            stats=_empty_stats(),
        )

    chapters = group_into_chapters(events)
    protagonist = _find_protagonist(events)
    stats = _compute_stats(events)
    period = period_label or _infer_period(events)

    title = _story_title(events, protagonist)
    summary = _story_summary(events, chapters, protagonist, stats)

    return CodeStory(
        title=title,
        period=period,
        chapters=chapters,
        protagonist=protagonist,
        summary=summary,
        stats=stats,
    )


def _find_protagonist(events: list[CommitEvent]) -> str:
    counts = Counter(e.author for e in events)
    return counts.most_common(1)[0][0] if counts else "unknown"


def _compute_stats(events: list[CommitEvent]) -> dict:
    if not events:
        return _empty_stats()

    day_counts: Counter[str] = Counter()
    for e in events:
        day_counts[e.timestamp.strftime("%Y-%m-%d")] += 1

    busiest_day = day_counts.most_common(1)[0][0] if day_counts else ""

    # Longest streak of consecutive days
    unique_days = sorted(set(e.timestamp.date() for e in events))
    longest_streak = _longest_streak(unique_days)

    return {
        "total_commits": len(events),
        "total_additions": sum(e.additions for e in events),
        "total_deletions": sum(e.deletions for e in events),
        "busiest_day": busiest_day,
        "longest_streak": longest_streak,
    }


def _empty_stats() -> dict:
    return {
        "total_commits": 0,
        "total_additions": 0,
        "total_deletions": 0,
        "busiest_day": "",
        "longest_streak": 0,
    }


def _longest_streak(dates: list) -> int:
    if not dates:
        return 0
    streak = 1
    max_streak = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 1
    return max_streak


def _infer_period(events: list[CommitEvent]) -> str:
    sorted_e = sorted(events, key=lambda e: e.timestamp)
    start = sorted_e[0].timestamp.strftime("%b %d")
    end = sorted_e[-1].timestamp.strftime("%b %d, %Y")
    return f"{start} - {end}"


def _story_title(events: list[CommitEvent], protagonist: str) -> str:
    types = Counter()
    for e in events:
        prefix = e.message.split(":")[0].split("(")[0].strip().lower() if ":" in e.message else "misc"
        types[prefix] += 1

    dominant = types.most_common(1)[0][0] if types else "misc"

    title_options = {
        "feat": f"The Week {protagonist} Built the Future",
        "fix": f"The Bug Hunt: {protagonist}'s Quest for Stability",
        "refactor": f"The Great Refactoring, by {protagonist}",
        "test": f"Fortifying the Kingdom: {protagonist}'s Testing Saga",
        "docs": f"The Scribe's Week: {protagonist} Documents Everything",
        "chore": f"The Unsung Work of {protagonist}",
    }

    return title_options.get(dominant, f"The Adventures of {protagonist} in Code")


def _story_summary(
    events: list[CommitEvent],
    chapters: list[StoryChapter],
    protagonist: str,
    stats: dict,
) -> str:
    total = stats["total_commits"]
    adds = stats["total_additions"]
    dels = stats["total_deletions"]
    streak = stats["longest_streak"]

    lines = []
    lines.append(
        f"Over this period, {protagonist} made {total} commits "
        f"across {len(chapters)} chapters of development."
    )
    lines.append(
        f"The codebase grew by {adds} lines and shrank by {dels} -- "
        f"a net change of {adds - dels:+d} lines."
    )
    if streak > 1:
        lines.append(f"An impressive {streak}-day coding streak kept the momentum alive.")

    moods = [ch.mood for ch in chapters]
    if "energetic" in moods:
        lines.append("There were bursts of intense energy.")
    if "debugging" in moods:
        lines.append("Some chapters were defined by the hunt for bugs.")
    if "breakthrough" in moods:
        lines.append("At least one breakthrough moment lit up the timeline.")

    return " ".join(lines)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_story_text(story: CodeStory) -> str:
    """Rich terminal narrative with chapters."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append(f"  {story.title}")
    lines.append(f"  Period: {story.period}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(story.summary)
    lines.append("")

    for i, ch in enumerate(story.chapters, 1):
        lines.append(f"--- Chapter {i}: {ch.title} ---")
        lines.append(f"    [{ch.timespan}] | Mood: {ch.mood}")
        lines.append(f"    {ch.narrative}")
        lines.append("")
        for ev in ch.events:
            ts = ev.timestamp.strftime("%H:%M")
            lines.append(f"    {ts}  {ev.message}  (+{ev.additions}/-{ev.deletions})")
        lines.append("")

    lines.append("-" * 60)
    lines.append("Stats:")
    lines.append(f"  Commits: {story.stats.get('total_commits', 0)}")
    lines.append(f"  Lines added: {story.stats.get('total_additions', 0)}")
    lines.append(f"  Lines removed: {story.stats.get('total_deletions', 0)}")
    lines.append(f"  Busiest day: {story.stats.get('busiest_day', 'N/A')}")
    lines.append(f"  Longest streak: {story.stats.get('longest_streak', 0)} days")
    lines.append("-" * 60)

    return "\n".join(lines)


def format_story_slack(story: CodeStory) -> str:
    """Slack mrkdwn format (*bold* not **bold**)."""
    lines: list[str] = []
    lines.append(f"*{story.title}*")
    lines.append(f"_{story.period}_")
    lines.append("")
    lines.append(story.summary)
    lines.append("")

    for i, ch in enumerate(story.chapters, 1):
        lines.append(f"*Chapter {i}: {ch.title}*")
        lines.append(f"_{ch.timespan}_ | Mood: {ch.mood}")
        lines.append(ch.narrative)
        lines.append("")

    lines.append(
        f"*Stats*: {story.stats.get('total_commits', 0)} commits | "
        f"+{story.stats.get('total_additions', 0)}/-{story.stats.get('total_deletions', 0)} lines | "
        f"Streak: {story.stats.get('longest_streak', 0)} days"
    )

    return "\n".join(lines)


def format_story_json(story: CodeStory) -> str:
    """JSON serialization of the story."""

    def _serialize(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Not serializable: {type(obj)}")

    data = asdict(story)
    return json.dumps(data, indent=2, default=_serialize)


def format_story_digest(story: CodeStory) -> str:
    """Short 3-5 line summary."""
    lines: list[str] = []
    lines.append(story.title)
    lines.append(
        f"{story.stats.get('total_commits', 0)} commits by {story.protagonist} "
        f"over {story.period}."
    )

    moods = list({ch.mood for ch in story.chapters})
    if moods:
        lines.append(f"Vibes: {', '.join(moods)}.")

    net = story.stats.get("total_additions", 0) - story.stats.get("total_deletions", 0)
    lines.append(f"Net impact: {net:+d} lines across the codebase.")

    streak = story.stats.get("longest_streak", 0)
    if streak > 1:
        lines.append(f"Longest coding streak: {streak} days.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_FORMATTERS = {
    "text": format_story_text,
    "slack": format_story_slack,
    "json": format_story_json,
    "digest": format_story_digest,
}


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Code Storyteller -- turn git logs into narratives."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to look back (default: 7)",
    )
    parser.add_argument(
        "--author",
        type=str,
        default=None,
        help="Filter commits to a single author",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=list(_FORMATTERS.keys()),
        default="text",
        help="Output format (default: text)",
    )

    args = parser.parse_args(argv)
    events = extract_events(days=args.days, author=args.author)
    story = build_story(events, period_label=f"Last {args.days} days")
    formatter = _FORMATTERS[args.format]
    print(formatter(story))


if __name__ == "__main__":
    main()
