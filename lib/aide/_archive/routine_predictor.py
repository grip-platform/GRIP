"""
Routine Predictor for GRIP Copilot.

Builds on AdaptiveLearner to provide proactive routine suggestions.
Analyzes time-of-day, sequence, and day-of-week patterns to predict
user needs before they ask.

Features:
- Time-based predictions (e.g., "user always runs standup at 9am")
- Sequence predictions (e.g., "after merging PR, user runs tests")
- Day-of-week habits (e.g., "Fridays = code review day")
- Routine health score (0-100) measuring consistency
- Multi-format output: text (CLI), Slack mrkdwn, JSON (MCP)

Usage:
    from aide.routine_predictor import RoutinePredictor
    predictor = RoutinePredictor()
    suggestions = predictor.predict_next(context={"last_event": "commit"})
    health = predictor.get_routine_health()
    print(predictor.format_suggestions_text(suggestions))
"""

import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from .adaptive_learning import AdaptiveLearner, UserEvent, LearnedRoutine
except ImportError:
    from adaptive_learning import AdaptiveLearner, UserEvent, LearnedRoutine

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DATA_DIR = CLAUDE_DIR / "data" / "aide"


@dataclass
class RoutineSuggestion:
    """A proactive suggestion based on learned routines."""
    action: str
    reason: str
    confidence: float  # 0-1
    trigger_type: str  # time, sequence, day, context
    suggested_command: str = ""
    timing: str = ""  # "now", "in 1 hour", "today at 14:00", etc.
    priority: str = "medium"  # low, medium, high
    context: dict = field(default_factory=dict)


class RoutinePredictor:
    """Proactive routine prediction engine."""

    def __init__(self, data_dir: Path = None):
        self.learner = AdaptiveLearner(data_dir=data_dir)
        self.data_dir = data_dir or DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def observe(self, event: str, context: dict = None, session_id: str = ""):
        """
        Record an event observation for learning.

        Args:
            event: Event type (commit, pr_create, pr_merge, mode_switch, etc.)
            context: Additional context (mode, files, metadata)
            session_id: Optional session identifier

        Returns:
            UserEvent that was recorded
        """
        return self.learner.record_event(event, context or {}, session_id)

    def predict_next(self, context: dict = None) -> list:
        """
        Predict next actions based on current context.

        Args:
            context: Current context dict with optional keys:
                - last_event: Most recent event type
                - hour: Current hour (0-23), defaults to now
                - day: Current day (0-6), defaults to now
                - mode: Current GRIP mode

        Returns:
            List of RoutineSuggestion objects sorted by confidence
        """
        context = context or {}
        now = datetime.now(timezone.utc)
        hour = context.get("hour", now.hour)
        day = context.get("day", now.weekday())
        last_event = context.get("last_event")

        routines = self.learner._load_routines()
        if not routines:
            routines = self.learner.detect_routines()

        suggestions = []

        for routine in routines:
            if not routine.is_reliable:
                continue

            # Time-based suggestions
            if routine.trigger == "time-based":
                routine_hour = routine.pattern.get("hour")
                if routine_hour == hour:
                    suggestions.append(RoutineSuggestion(
                        action=routine.pattern["action"],
                        reason=routine.description,
                        confidence=routine.confidence,
                        trigger_type="time",
                        timing="now",
                        priority=self._calculate_priority(routine.confidence, "time"),
                        suggested_command=self._suggest_command(routine.pattern["action"]),
                        context={"hour": hour},
                    ))
                elif abs(routine_hour - hour) == 1:
                    # Suggest actions coming up in the next hour
                    suggestions.append(RoutineSuggestion(
                        action=routine.pattern["action"],
                        reason=f"Coming up: {routine.description}",
                        confidence=routine.confidence * 0.8,  # Slight reduction for future
                        trigger_type="time",
                        timing=f"at {routine_hour:02d}:00",
                        priority="low",
                        suggested_command=self._suggest_command(routine.pattern["action"]),
                        context={"hour": routine_hour},
                    ))

            # Sequence-based suggestions
            elif routine.trigger == "sequence-based" and last_event:
                if routine.pattern.get("before") == last_event:
                    suggestions.append(RoutineSuggestion(
                        action=routine.pattern["after"],
                        reason=routine.description,
                        confidence=routine.confidence,
                        trigger_type="sequence",
                        timing="now",
                        priority=self._calculate_priority(routine.confidence, "sequence"),
                        suggested_command=self._suggest_command(routine.pattern["after"]),
                        context={"after_event": last_event},
                    ))

            # Day-based suggestions
            elif routine.trigger == "day-based":
                if routine.pattern.get("day") == day:
                    suggestions.append(RoutineSuggestion(
                        action=routine.pattern["action"],
                        reason=routine.description,
                        confidence=routine.confidence,
                        trigger_type="day",
                        timing="today",
                        priority=self._calculate_priority(routine.confidence, "day"),
                        suggested_command=self._suggest_command(routine.pattern["action"]),
                        context={"day": routine.pattern.get("day_name", "")},
                    ))

            # Context-based suggestions (mode preferences)
            elif routine.trigger == "context-based":
                current_mode = context.get("mode")
                if current_mode and routine.pattern.get("mode") == current_mode:
                    suggestions.append(RoutineSuggestion(
                        action=routine.pattern["action"],
                        reason=routine.description,
                        confidence=routine.confidence,
                        trigger_type="context",
                        timing="now",
                        priority=self._calculate_priority(routine.confidence, "context"),
                        suggested_command=self._suggest_command(routine.pattern["action"]),
                        context={"mode": current_mode},
                    ))

        # Sort by priority then confidence
        priority_order = {"high": 0, "medium": 1, "low": 2}
        suggestions.sort(key=lambda s: (priority_order[s.priority], -s.confidence))

        # Deduplicate by action
        seen = set()
        unique = []
        for s in suggestions:
            if s.action not in seen:
                seen.add(s.action)
                unique.append(s)

        return unique[:10]  # Top 10 suggestions

    def get_routine_health(self, days: int = 30) -> dict:
        """
        Calculate routine health score (0-100).

        Measures how consistent the user's routines are.
        High score = predictable, low score = chaotic.

        Returns:
            Dict with:
                - score: 0-100
                - consistency: str description
                - metrics: breakdown of scoring factors
        """
        events = self.learner.get_events(days=days)
        if not events:
            return {
                "score": 0,
                "consistency": "no data",
                "metrics": {},
                "message": "No events tracked yet. Start using GRIP to build routines.",
            }

        routines = self.learner._load_routines()
        if not routines:
            routines = self.learner.detect_routines(days=days)

        reliable_routines = [r for r in routines if r.is_reliable]

        # Scoring factors (each 0-100)
        scores = {}

        # 1. Pattern coverage: % of events that match a routine (0-35 points)
        matched_events = 0
        for event in events:
            for routine in reliable_routines:
                if routine.trigger == "time-based" and routine.pattern.get("hour") == event.hour:
                    if routine.pattern.get("action") == event.event_type:
                        matched_events += 1
                        break
                elif routine.trigger == "day-based" and routine.pattern.get("day") == event.day_of_week:
                    if routine.pattern.get("action") == event.event_type:
                        matched_events += 1
                        break

        coverage = matched_events / max(len(events), 1)
        scores["pattern_coverage"] = min(35, coverage * 35)

        # 2. Reliable routines count (0-25 points)
        # 3+ reliable = 25, 2 = 15, 1 = 8, 0 = 0
        routine_score = min(25, len(reliable_routines) * 8)
        scores["reliable_routines"] = routine_score

        # 3. Confidence average (0-20 points)
        if reliable_routines:
            avg_confidence = sum(r.confidence for r in reliable_routines) / len(reliable_routines)
            scores["avg_confidence"] = avg_confidence * 20
        else:
            scores["avg_confidence"] = 0

        # 4. Temporal consistency: variance in event timing (0-20 points)
        # Lower variance = higher score
        hour_events = defaultdict(int)
        for event in events:
            hour_events[event.hour] += 1

        if len(hour_events) > 0:
            mean_events = sum(hour_events.values()) / len(hour_events)
            variance = sum((count - mean_events) ** 2 for count in hour_events.values()) / len(hour_events)
            # Normalize: low variance (0-5) = high score, high variance (>20) = low score
            temporal_score = max(0, 20 - variance)
            scores["temporal_consistency"] = min(20, temporal_score)
        else:
            scores["temporal_consistency"] = 0

        # 5. Activity frequency: more events = more data = better (0-10 points)
        # 100+ events = 10, 50 = 5, <10 = 1
        event_count = len(events)
        if event_count >= 100:
            scores["activity_frequency"] = 10
        elif event_count >= 50:
            scores["activity_frequency"] = 5
        elif event_count >= 10:
            scores["activity_frequency"] = 3
        else:
            scores["activity_frequency"] = 1

        # Total score
        total = sum(scores.values())

        # Consistency rating
        if total >= 80:
            consistency = "excellent"
        elif total >= 60:
            consistency = "good"
        elif total >= 40:
            consistency = "moderate"
        elif total >= 20:
            consistency = "developing"
        else:
            consistency = "chaotic"

        return {
            "score": round(total),
            "consistency": consistency,
            "metrics": {k: round(v, 1) for k, v in scores.items()},
            "message": self._health_message(total, len(reliable_routines), coverage),
            "reliable_routines": len(reliable_routines),
            "total_events": event_count,
            "pattern_coverage": f"{coverage:.0%}",
        }

    def format_suggestions_text(self, suggestions: list) -> str:
        """
        Format suggestions as plain text for CLI.

        Args:
            suggestions: List of RoutineSuggestion objects

        Returns:
            Formatted text string
        """
        if not suggestions:
            return "No routine suggestions at this time.\nKeep using GRIP to build predictable patterns."

        lines = ["Routine Predictions", ""]

        for i, s in enumerate(suggestions, 1):
            priority_marker = {
                "high": "[!]",
                "medium": "[-]",
                "low": "[ ]",
            }[s.priority]

            lines.append(f"{i}. {priority_marker} {s.action.upper()} ({s.confidence:.0%} confidence)")
            lines.append(f"   Why: {s.reason}")
            lines.append(f"   When: {s.timing}")
            if s.suggested_command:
                lines.append(f"   Command: {s.suggested_command}")
            lines.append("")

        return "\n".join(lines)

    def format_suggestions_slack(self, suggestions: list) -> str:
        """
        Format suggestions as Slack mrkdwn.

        Uses proper Slack markdown (*bold* not **bold**).

        Args:
            suggestions: List of RoutineSuggestion objects

        Returns:
            Slack-formatted mrkdwn string
        """
        if not suggestions:
            return "*No routine suggestions at this time.*\nKeep using GRIP to build predictable patterns."

        lines = ["*Routine Predictions*", ""]

        for s in suggestions:
            priority_icon = {
                "high": ":red_circle:",
                "medium": ":large_blue_circle:",
                "low": ":white_circle:",
            }[s.priority]

            lines.append(f"{priority_icon} *{s.action.upper()}* ({s.confidence:.0%})")
            lines.append(f"  {s.reason}")
            if s.suggested_command:
                lines.append(f"  `{s.suggested_command}`")
            lines.append("")

        return "\n".join(lines)

    def format_suggestions_json(self, suggestions: list) -> str:
        """
        Format suggestions as JSON for MCP/API consumption.

        Args:
            suggestions: List of RoutineSuggestion objects

        Returns:
            JSON string
        """
        data = [asdict(s) for s in suggestions]
        return json.dumps(data, indent=2)

    def _calculate_priority(self, confidence: float, trigger_type: str) -> str:
        """Calculate suggestion priority based on confidence and trigger."""
        # Sequence-based = highest priority (user just did something)
        if trigger_type == "sequence":
            if confidence >= 0.8:
                return "high"
            elif confidence >= 0.6:
                return "medium"
            else:
                return "low"

        # Time-based = medium priority (it's the right time)
        elif trigger_type == "time":
            if confidence >= 0.9:
                return "high"
            elif confidence >= 0.7:
                return "medium"
            else:
                return "low"

        # Day/context-based = lower priority (broader pattern)
        else:
            if confidence >= 0.9:
                return "medium"
            else:
                return "low"

    def _suggest_command(self, action: str) -> str:
        """Map action type to suggested GRIP command."""
        command_map = {
            "commit": "git add . && git commit",
            "pr_create": "/create-pr",
            "pr_merge": "gh pr merge",
            "mode_switch": "/mode <name>",
            "skill_use": "/<skill-command>",
            "review": "/review-pr",
            "deploy": "python3 scripts/deploy-grip.sh",
            "standup": "/standup",
            "test": "pytest",
            "build": "npm run build",
            "sync": "git pull origin main",
        }
        return command_map.get(action, "")

    def _health_message(self, score: int, reliable_count: int, coverage: float) -> str:
        """Generate a helpful message based on health metrics."""
        if score >= 80:
            return f"Strong routine consistency. {reliable_count} reliable patterns detected with {coverage:.0%} coverage."
        elif score >= 60:
            return f"Good routine foundation. {reliable_count} patterns established. Keep it up."
        elif score >= 40:
            return f"Moderate routine consistency. {reliable_count} patterns emerging. More data will improve predictions."
        elif score >= 20:
            return "Developing routines. Keep using GRIP regularly to establish patterns."
        else:
            return "Chaotic workflow detected. Establishing routines will improve efficiency."


def main():
    """CLI entry point."""
    predictor = RoutinePredictor()

    if len(sys.argv) < 2:
        print("Usage: routine_predictor.py <command> [args]")
        print("Commands:")
        print("  predict [last_event=X] [hour=H] [day=D] [mode=M]  - Get predictions")
        print("  health [days=N]                                    - Get routine health score")
        print("  observe <event_type> [key=value ...]              - Record an observation")
        print("  text [context...]                                  - Predictions as text")
        print("  slack [context...]                                 - Predictions as Slack mrkdwn")
        print("  json [context...]                                  - Predictions as JSON")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "predict":
        # Parse context from args like last_event=commit hour=9
        context = {}
        for arg in sys.argv[2:]:
            if "=" in arg:
                k, v = arg.split("=", 1)
                # Try to convert to int for hour/day
                if k in ("hour", "day"):
                    try:
                        context[k] = int(v)
                    except ValueError:
                        context[k] = v
                else:
                    context[k] = v

        suggestions = predictor.predict_next(context)
        print(predictor.format_suggestions_text(suggestions))

    elif cmd == "health":
        days = 30
        for arg in sys.argv[2:]:
            if arg.startswith("days="):
                try:
                    days = int(arg.split("=")[1])
                except ValueError:
                    pass

        health = predictor.get_routine_health(days=days)
        print(f"Routine Health Score: {health['score']}/100 ({health['consistency']})")
        print()
        print(health["message"])
        print()

        if health["metrics"]:
            print("Metrics:")
            for metric, value in health["metrics"].items():
                print(f"  {metric}: {value:.1f}")
            print()

        if "reliable_routines" in health:
            print(f"Reliable routines: {health['reliable_routines']}")
            print(f"Total events: {health['total_events']}")
            print(f"Pattern coverage: {health['pattern_coverage']}")

    elif cmd == "observe":
        if len(sys.argv) < 3:
            print("Usage: routine_predictor.py observe <event_type> [key=value ...]")
            sys.exit(1)

        event_type = sys.argv[2]
        context = {}
        for arg in sys.argv[3:]:
            if "=" in arg:
                k, v = arg.split("=", 1)
                context[k] = v

        event = predictor.observe(event_type, context)
        print(f"Observed: {event.event_type} at {event.timestamp}")

    elif cmd == "text":
        context = {}
        for arg in sys.argv[2:]:
            if "=" in arg:
                k, v = arg.split("=", 1)
                if k in ("hour", "day"):
                    try:
                        context[k] = int(v)
                    except ValueError:
                        context[k] = v
                else:
                    context[k] = v

        suggestions = predictor.predict_next(context)
        print(predictor.format_suggestions_text(suggestions))

    elif cmd == "slack":
        context = {}
        for arg in sys.argv[2:]:
            if "=" in arg:
                k, v = arg.split("=", 1)
                if k in ("hour", "day"):
                    try:
                        context[k] = int(v)
                    except ValueError:
                        context[k] = v
                else:
                    context[k] = v

        suggestions = predictor.predict_next(context)
        print(predictor.format_suggestions_slack(suggestions))

    elif cmd == "json":
        context = {}
        for arg in sys.argv[2:]:
            if "=" in arg:
                k, v = arg.split("=", 1)
                if k in ("hour", "day"):
                    try:
                        context[k] = int(v)
                    except ValueError:
                        context[k] = v
                else:
                    context[k] = v

        suggestions = predictor.predict_next(context)
        print(predictor.format_suggestions_json(suggestions))

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: predict, health, observe, text, slack, json")
        sys.exit(1)


if __name__ == "__main__":
    main()
