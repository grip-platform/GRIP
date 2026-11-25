#!/usr/bin/env python3
"""Commit Mood Analyzer -- sentiment analysis of commit messages.

Tracks developer mood and energy over time using keyword-based analysis
of git commit messages. No ML dependencies -- pure Python stdlib.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MOOD_CATEGORIES = {
    "energized": {
        "keywords": [
            "awesome", "great", "nailed", "perfect", "excellent", "amazing",
            "beautiful", "brilliant", "fantastic", "incredible", "love",
            "boom", "crushing", "superb", "stellar", "sweet", "woohoo",
            "hell yeah", "rock", "ship it", "done done",
        ],
        "patterns": [r"!{1,}", r"\b[A-Z]{3,}\b"],
        "emoji": "lightning",
        "display_emoji": "zap",
    },
    "focused": {
        "keywords": [
            "refactor", "extract", "rename", "inline", "optimize",
            "implement", "add", "remove", "move", "split", "simplify",
            "consolidate", "restructure", "decouple", "encapsulate",
            "abstract", "modularize", "parametrize",
        ],
        "patterns": [r"^\w+:\s\S+"],
        "emoji": "bullseye",
        "display_emoji": "dart",
    },
    "frustrated": {
        "keywords": [
            "fix", "again", "finally", "hack", "workaround", "revert",
            "broken", "bug", "damn", "ugh", "sigh", "ffs", "wtf",
            "bandaid", "band-aid", "kludge", "duct tape", "TODO",
            "temporary", "regression", "stupid", "why", "annoying",
            "still broken", "yet another",
        ],
        "patterns": [r"fix\s+fix", r"attempt\s+\d+", r"try\s+#?\d+"],
        "emoji": "face_with_steam",
        "display_emoji": "persevere",
    },
    "creative": {
        "keywords": [
            "new", "feat", "feature", "design", "experiment", "prototype",
            "explore", "concept", "idea", "draft", "spike", "poc",
            "proof of concept", "brainstorm", "novel", "innovative",
            "rethink", "reimagine", "invent", "create", "build",
        ],
        "patterns": [r"\bwip\b", r"\bpoc\b"],
        "emoji": "art",
        "display_emoji": "art",
    },
    "maintenance": {
        "keywords": [
            "chore", "update", "bump", "cleanup", "clean up", "tidy",
            "lint", "format", "deps", "dependencies", "version",
            "upgrade", "downgrade", "pin", "lock", "migrate", "migration",
            "housekeeping", "maintenance", "routine", "prune",
        ],
        "patterns": [r"bump\s+v?\d", r"\d+\.\d+\.\d+"],
        "emoji": "wrench",
        "display_emoji": "wrench",
    },
    "urgent": {
        "keywords": [
            "hotfix", "critical", "emergency", "asap", "urgent", "p0",
            "outage", "incident", "production", "prod", "rollback",
            "severity", "sev1", "sev0", "on fire", "broken prod",
            "deploy now", "push now",
        ],
        "patterns": [r"\bP0\b", r"\bSEV[01]\b", r"!!!"],
        "emoji": "rotating_light",
        "display_emoji": "rotating_light",
    },
    "collaborative": {
        "keywords": [
            "pair", "review", "discuss", "merge", "co-authored",
            "feedback", "suggestion", "team", "together", "sync",
            "align", "handoff", "hand-off", "mob", "ensemble",
            "pairing", "collab", "collaborate",
        ],
        "patterns": [r"co-authored-by", r"pair\s+with", r"reviewed\s+by"],
        "emoji": "handshake",
        "display_emoji": "handshake",
    },
}

MOOD_NEUTRAL = "neutral"

# Burnout detection thresholds
BURNOUT_FRUSTRATED_RATIO_THRESHOLD = 0.35
BURNOUT_MSG_LENGTH_DECLINE_THRESHOLD = 0.30
BURNOUT_MAINTENANCE_RATIO_THRESHOLD = 0.50
BURNOUT_ODD_HOURS_RATIO_THRESHOLD = 0.40
ODD_HOURS = set(range(0, 6))  # midnight to 5am


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MoodResult:
    mood: str
    confidence: float
    keywords_matched: list[str] = field(default_factory=list)
    patterns_matched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CommitMood:
    hash: str
    author: str
    timestamp: datetime
    message: str
    mood: MoodResult = field(default_factory=lambda: MoodResult(MOOD_NEUTRAL, 0.0))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class MoodSummary:
    total_commits: int = 0
    dominant_mood: str = MOOD_NEUTRAL
    mood_distribution: dict[str, int] = field(default_factory=dict)
    mood_percentages: dict[str, float] = field(default_factory=dict)
    avg_confidence: float = 0.0
    mood_shifts: int = 0
    burnout_risk: float = 0.0
    period_days: int = 30

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BurnoutIndicators:
    frustrated_ratio: float = 0.0
    frustrated_trending_up: bool = False
    avg_message_length_early: float = 0.0
    avg_message_length_late: float = 0.0
    message_length_declining: bool = False
    odd_hours_ratio: float = 0.0
    odd_hours_increasing: bool = False
    maintenance_ratio: float = 0.0
    maintenance_trending_up: bool = False
    overall_risk: float = 0.0
    risk_level: str = "low"
    indicators_triggered: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Git interaction
# ---------------------------------------------------------------------------

_LOG_SEP = "---MOOD_SEP---"
_FIELD_SEP = "---FIELD_SEP---"
_GIT_LOG_FORMAT = _FIELD_SEP.join(["%H", "%an", "%aI", "%s"])


def _run_git(args: list[str]) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _parse_git_log(raw: str) -> list[CommitMood]:
    """Parse raw git log output into CommitMood objects."""
    commits: list[CommitMood] = []
    for entry in raw.strip().split(_LOG_SEP):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(_FIELD_SEP)
        if len(parts) < 4:
            continue
        hash_, author, ts_str, message = parts[0], parts[1], parts[2], parts[3]
        try:
            ts = datetime.fromisoformat(ts_str)
        except (ValueError, IndexError):
            continue
        commits.append(CommitMood(
            hash=hash_.strip(),
            author=author.strip(),
            timestamp=ts,
            message=message.strip(),
        ))
    return commits


# ---------------------------------------------------------------------------
# Mood Analyzer
# ---------------------------------------------------------------------------

class MoodAnalyzer:
    """Analyze commit message sentiment to track developer mood over time."""

    def __init__(
        self,
        categories: Optional[dict] = None,
        git_runner=None,
    ):
        self.categories = categories or MOOD_CATEGORIES
        self._git = git_runner or _run_git
        self._history_cache: Optional[list[CommitMood]] = None

    # -- Core analysis -------------------------------------------------------

    def analyze_commit(self, message: str) -> MoodResult:
        """Analyze a single commit message and return mood + confidence."""
        if not message or not message.strip():
            return MoodResult(mood=MOOD_NEUTRAL, confidence=0.0)

        msg_lower = message.lower().strip()
        scores: dict[str, float] = {}
        matched_keywords: dict[str, list[str]] = defaultdict(list)
        matched_patterns: dict[str, list[str]] = defaultdict(list)

        for mood, cfg in self.categories.items():
            score = 0.0

            # Keyword matching
            for kw in cfg["keywords"]:
                if kw.lower() in msg_lower:
                    score += 1.0
                    matched_keywords[mood].append(kw)

            # Pattern matching
            for pat in cfg.get("patterns", []):
                if re.search(pat, message, re.IGNORECASE):
                    score += 0.5
                    matched_patterns[mood].append(pat)

            # Exclamation boost for energized
            if mood == "energized":
                excl_count = message.count("!")
                if excl_count > 0:
                    score += min(excl_count * 0.3, 1.5)

            # Short message boost for focused (< 50 chars, no fluff)
            # Reduced to avoid focused dominating other categories
            if mood == "focused" and len(message) < 50 and ":" in message:
                score += 0.2

            if score > 0:
                scores[mood] = score

        if not scores:
            return MoodResult(mood=MOOD_NEUTRAL, confidence=0.1)

        best_mood = max(scores, key=scores.get)  # type: ignore[arg-type]
        best_score = scores[best_mood]

        # Confidence: normalize to 0.0-1.0
        # 1 keyword match = ~0.3, 3+ = ~0.7, 5+ = ~0.9
        total_signals = best_score
        confidence = min(0.95, total_signals / (total_signals + 2.0))
        confidence = round(confidence, 2)

        return MoodResult(
            mood=best_mood,
            confidence=confidence,
            keywords_matched=matched_keywords.get(best_mood, []),
            patterns_matched=matched_patterns.get(best_mood, []),
        )

    # -- History analysis ----------------------------------------------------

    def analyze_history(
        self,
        days: int = 30,
        author: Optional[str] = None,
    ) -> list[CommitMood]:
        """Scan git log and return mood-annotated timeline."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        args = [
            "log",
            f"--since={since}",
            f"--format={_LOG_SEP}{_GIT_LOG_FORMAT}",
            "--no-merges",
        ]
        if author:
            args.append(f"--author={author}")

        raw = self._git(args)
        commits = _parse_git_log(raw)

        for commit in commits:
            commit.mood = self.analyze_commit(commit.message)

        self._history_cache = commits
        return commits

    def analyze_commits(self, commits: list[CommitMood]) -> list[CommitMood]:
        """Analyze mood for a list of pre-built CommitMood objects."""
        for commit in commits:
            commit.mood = self.analyze_commit(commit.message)
        self._history_cache = commits
        return commits

    # -- Summary & burnout ---------------------------------------------------

    def get_mood_summary(
        self,
        commits: Optional[list[CommitMood]] = None,
    ) -> MoodSummary:
        """Aggregate mood stats from commit history."""
        commits = commits or self._history_cache or []
        if not commits:
            return MoodSummary()

        moods = [c.mood.mood for c in commits]
        mood_counts = Counter(moods)
        total = len(moods)

        dominant = mood_counts.most_common(1)[0][0] if mood_counts else MOOD_NEUTRAL

        percentages = {m: round(c / total * 100, 1) for m, c in mood_counts.items()}

        confidences = [c.mood.confidence for c in commits]
        avg_conf = round(sum(confidences) / len(confidences), 2) if confidences else 0.0

        # Count mood shifts (consecutive commits with different moods)
        shifts = 0
        for i in range(1, len(moods)):
            if moods[i] != moods[i - 1]:
                shifts += 1

        # Calculate period
        if len(commits) >= 2:
            ts_sorted = sorted(c.timestamp for c in commits)
            delta = ts_sorted[-1] - ts_sorted[0]
            period_days = max(1, delta.days)
        else:
            period_days = 1

        burnout = self.get_burnout_indicators(commits)

        return MoodSummary(
            total_commits=total,
            dominant_mood=dominant,
            mood_distribution=dict(mood_counts),
            mood_percentages=percentages,
            avg_confidence=avg_conf,
            mood_shifts=shifts,
            burnout_risk=burnout.overall_risk,
            period_days=period_days,
        )

    @staticmethod
    def _half_trend_rising(values: list, total: int, threshold: float = 0.2) -> bool:
        """Check if second-half ratio exceeds first-half and a threshold."""
        if total < 4:
            return False
        mid = total // 2
        first_count = sum(values[:mid])
        second_count = sum(values[mid:])
        first_r = first_count / mid if mid else 0
        second_r = second_count / (total - mid) if (total - mid) else 0
        return second_r > first_r and second_r > threshold

    def _check_ratios(self, indicators, moods, commits, triggered):
        """Compute ratio-based burnout signals. Returns cumulative risk."""
        total = len(moods)
        risk = 0.0

        # Frustrated ratio
        frustrated_count = moods.count("frustrated")
        indicators.frustrated_ratio = round(frustrated_count / total, 2) if total else 0.0
        if indicators.frustrated_ratio >= BURNOUT_FRUSTRATED_RATIO_THRESHOLD:
            triggered.append("high_frustration_ratio")
            risk += 1.0

        # Odd hours ratio
        odd_count = sum(1 for c in commits if c.timestamp.hour in ODD_HOURS)
        indicators.odd_hours_ratio = round(odd_count / total, 2) if total else 0.0
        if indicators.odd_hours_ratio >= BURNOUT_ODD_HOURS_RATIO_THRESHOLD:
            triggered.append("high_odd_hours_ratio")
            risk += 0.6

        # Maintenance ratio
        maint_count = moods.count("maintenance")
        indicators.maintenance_ratio = round(maint_count / total, 2) if total else 0.0
        if indicators.maintenance_ratio >= BURNOUT_MAINTENANCE_RATIO_THRESHOLD:
            triggered.append("high_maintenance_ratio")
            risk += 0.6

        return risk

    def _check_trends(self, indicators, moods, commits, triggered):
        """Compute trend-based burnout signals. Returns cumulative risk."""
        total = len(moods)
        risk = 0.0

        frustrated_flags = [1 if m == "frustrated" else 0 for m in moods]
        if self._half_trend_rising(frustrated_flags, total, 0.2):
            indicators.frustrated_trending_up = True
            triggered.append("frustration_trending_up")
            risk += 0.8

        odd_flags = [1 if c.timestamp.hour in ODD_HOURS else 0 for c in commits]
        if self._half_trend_rising(odd_flags, total, 0.2):
            indicators.odd_hours_increasing = True
            triggered.append("odd_hours_increasing")
            risk += 0.5

        maint_flags = [1 if m == "maintenance" else 0 for m in moods]
        if self._half_trend_rising(maint_flags, total, 0.3):
            indicators.maintenance_trending_up = True
            triggered.append("maintenance_trending_up")
            risk += 0.5

        # Message length declining
        if total >= 4:
            mid = total // 2
            early_msgs = [c.message for c in commits[:mid]]
            late_msgs = [c.message for c in commits[mid:]]
            indicators.avg_message_length_early = (
                sum(len(m) for m in early_msgs) / len(early_msgs) if early_msgs else 0.0
            )
            indicators.avg_message_length_late = (
                sum(len(m) for m in late_msgs) / len(late_msgs) if late_msgs else 0.0
            )
            if (
                indicators.avg_message_length_early > 0
                and indicators.avg_message_length_late
                < indicators.avg_message_length_early * (1 - BURNOUT_MSG_LENGTH_DECLINE_THRESHOLD)
            ):
                indicators.message_length_declining = True
                triggered.append("message_length_declining")
                risk += 0.7

        return risk

    def get_burnout_indicators(
        self,
        commits: Optional[list[CommitMood]] = None,
    ) -> BurnoutIndicators:
        """Detect patterns suggesting burnout."""
        commits = commits or self._history_cache or []
        if not commits:
            return BurnoutIndicators()

        indicators = BurnoutIndicators()
        triggered: list[str] = []
        moods = [c.mood.mood for c in commits]

        risk = self._check_ratios(indicators, moods, commits, triggered)
        risk += self._check_trends(indicators, moods, commits, triggered)

        # Overall risk: 0.0 to 1.0
        max_possible = 4.7  # sum of all individual risk weights
        indicators.overall_risk = round(min(1.0, risk / max_possible), 2)
        indicators.indicators_triggered = triggered

        if indicators.overall_risk >= 0.6:
            indicators.risk_level = "high"
        elif indicators.overall_risk >= 0.3:
            indicators.risk_level = "medium"
        else:
            indicators.risk_level = "low"

        return indicators

    # -- Formatting ----------------------------------------------------------

    def format_mood_report(
        self,
        commits: Optional[list[CommitMood]] = None,
    ) -> str:
        """Pretty-print mood report with indicators."""
        commits = commits or self._history_cache or []
        if not commits:
            return "No commits to analyze."

        summary = self.get_mood_summary(commits)
        burnout = self.get_burnout_indicators(commits)

        mood_emojis = {
            "energized": "[zap]",
            "focused": "[dart]",
            "frustrated": "[persevere]",
            "creative": "[art]",
            "maintenance": "[wrench]",
            "urgent": "[rotating_light]",
            "collaborative": "[handshake]",
            "neutral": "[neutral_face]",
        }

        lines = [
            "COMMIT MOOD REPORT",
            "=" * 50,
            f"Period: {summary.period_days} days | Commits: {summary.total_commits}",
            f"Dominant mood: {summary.dominant_mood} {mood_emojis.get(summary.dominant_mood, '')}",
            f"Avg confidence: {summary.avg_confidence}",
            f"Mood shifts: {summary.mood_shifts}",
            "",
            "MOOD DISTRIBUTION",
            "-" * 30,
        ]

        for mood, pct in sorted(
            summary.mood_percentages.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            bar_len = int(pct / 5)
            bar = "#" * bar_len
            emoji = mood_emojis.get(mood, "")
            lines.append(f"  {emoji} {mood:15s} {pct:5.1f}% {bar}")

        lines.extend([
            "",
            "BURNOUT INDICATORS",
            "-" * 30,
            f"  Overall risk: {burnout.risk_level.upper()} ({burnout.overall_risk:.0%})",
            f"  Frustrated ratio: {burnout.frustrated_ratio:.0%}",
            f"  Odd hours ratio: {burnout.odd_hours_ratio:.0%}",
            f"  Maintenance ratio: {burnout.maintenance_ratio:.0%}",
            f"  Message length declining: {'Yes' if burnout.message_length_declining else 'No'}",
        ])

        if burnout.indicators_triggered:
            lines.append(f"  Triggers: {', '.join(burnout.indicators_triggered)}")

        return "\n".join(lines)

    def format_slack_message(
        self,
        commits: Optional[list[CommitMood]] = None,
    ) -> str:
        """Format mood summary as Slack mrkdwn (uses *bold* not **bold**)."""
        commits = commits or self._history_cache or []
        if not commits:
            return "No commits to analyze."

        summary = self.get_mood_summary(commits)
        burnout = self.get_burnout_indicators(commits)

        slack_emojis = {
            "energized": ":zap:",
            "focused": ":dart:",
            "frustrated": ":persevere:",
            "creative": ":art:",
            "maintenance": ":wrench:",
            "urgent": ":rotating_light:",
            "collaborative": ":handshake:",
            "neutral": ":neutral_face:",
        }

        emoji = slack_emojis.get(summary.dominant_mood, ":speech_balloon:")

        lines = [
            f"{emoji} *Commit Mood Report*",
            f"*{summary.total_commits}* commits over *{summary.period_days}* days",
            f"Dominant mood: *{summary.dominant_mood}*",
            "",
        ]

        for mood, pct in sorted(
            summary.mood_percentages.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            m_emoji = slack_emojis.get(mood, "")
            lines.append(f"{m_emoji} {mood}: *{pct:.1f}%*")

        if burnout.risk_level != "low":
            lines.extend([
                "",
                f":warning: *Burnout risk: {burnout.risk_level.upper()}* ({burnout.overall_risk:.0%})",
            ])
            if burnout.indicators_triggered:
                for indicator in burnout.indicators_triggered:
                    lines.append(f"  - {indicator.replace('_', ' ')}")

        return "\n".join(lines)

    # -- MCP generators ------------------------------------------------------

    def generate_mcp_timeline(
        self,
        commits: Optional[list[CommitMood]] = None,
    ) -> dict:
        """Return dict for grip_timeline MCP app showing mood over time."""
        commits = commits or self._history_cache or []

        events = []
        for c in commits:
            events.append({
                "date": c.timestamp.isoformat(),
                "label": f"{c.mood.mood} ({c.mood.confidence:.0%})",
                "category": c.mood.mood,
                "detail": c.message[:80],
                "hash": c.hash[:8],
                "author": c.author,
            })

        # Daily mood aggregation
        daily: dict[str, list[str]] = defaultdict(list)
        for c in commits:
            day_key = c.timestamp.strftime("%Y-%m-%d")
            daily[day_key].append(c.mood.mood)

        daily_dominant = {}
        for day, moods in sorted(daily.items()):
            counter = Counter(moods)
            daily_dominant[day] = {
                "dominant": counter.most_common(1)[0][0],
                "count": len(moods),
                "distribution": dict(counter),
            }

        return {
            "type": "mood_timeline",
            "title": "Commit Mood Timeline",
            "events": events,
            "daily_summary": daily_dominant,
            "total_commits": len(commits),
        }

    def generate_mcp_digest(
        self,
        commits: Optional[list[CommitMood]] = None,
    ) -> dict:
        """Return dict for grip_digest MCP app."""
        commits = commits or self._history_cache or []
        summary = self.get_mood_summary(commits)
        burnout = self.get_burnout_indicators(commits)

        return {
            "type": "mood_digest",
            "title": "Developer Mood Digest",
            "summary": {
                "total_commits": summary.total_commits,
                "dominant_mood": summary.dominant_mood,
                "mood_distribution": summary.mood_percentages,
                "avg_confidence": summary.avg_confidence,
                "mood_shifts": summary.mood_shifts,
                "period_days": summary.period_days,
            },
            "burnout": {
                "risk_level": burnout.risk_level,
                "overall_risk": burnout.overall_risk,
                "indicators": burnout.indicators_triggered,
            },
            "highlights": self._generate_highlights(commits),
        }

    def _generate_highlights(self, commits: list[CommitMood]) -> list[dict]:
        """Extract notable mood events for the digest."""
        highlights = []

        # Highest confidence commits
        if commits:
            by_conf = sorted(commits, key=lambda c: c.mood.confidence, reverse=True)
            for c in by_conf[:3]:
                highlights.append({
                    "type": "high_confidence",
                    "mood": c.mood.mood,
                    "confidence": c.mood.confidence,
                    "message": c.message[:80],
                    "date": c.timestamp.isoformat(),
                })

        # Mood streaks (3+ consecutive same mood)
        if len(commits) >= 3:
            streak_mood = commits[0].mood.mood
            streak_start = 0
            streak_len = 1
            for i in range(1, len(commits)):
                if commits[i].mood.mood == streak_mood:
                    streak_len += 1
                else:
                    if streak_len >= 3:
                        highlights.append({
                            "type": "mood_streak",
                            "mood": streak_mood,
                            "length": streak_len,
                            "start_date": commits[streak_start].timestamp.isoformat(),
                        })
                    streak_mood = commits[i].mood.mood
                    streak_start = i
                    streak_len = 1
            if streak_len >= 3:
                highlights.append({
                    "type": "mood_streak",
                    "mood": streak_mood,
                    "length": streak_len,
                    "start_date": commits[streak_start].timestamp.isoformat(),
                })

        return highlights


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Commit Mood Analyzer")
    parser.add_argument("--days", type=int, default=30, help="Days of history")
    parser.add_argument("--author", type=str, default=None, help="Filter by author")
    parser.add_argument(
        "--format",
        choices=["text", "slack", "json", "timeline", "digest"],
        default="text",
        help="Output format",
    )
    parser.add_argument("--message", type=str, help="Analyze a single message")

    args = parser.parse_args()
    analyzer = MoodAnalyzer()

    if args.message:
        result = analyzer.analyze_commit(args.message)
        if args.format == "json":
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(f"Mood: {result.mood} (confidence: {result.confidence})")
            if result.keywords_matched:
                print(f"Keywords: {', '.join(result.keywords_matched)}")
        return

    try:
        commits = analyzer.analyze_history(days=args.days, author=args.author)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.format == "text":
        print(analyzer.format_mood_report(commits))
    elif args.format == "slack":
        print(analyzer.format_slack_message(commits))
    elif args.format == "json":
        print(json.dumps([c.to_dict() for c in commits], indent=2))
    elif args.format == "timeline":
        print(json.dumps(analyzer.generate_mcp_timeline(commits), indent=2))
    elif args.format == "digest":
        print(json.dumps(analyzer.generate_mcp_digest(commits), indent=2))


if __name__ == "__main__":
    main()
