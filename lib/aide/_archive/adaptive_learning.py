"""
Adaptive Learning Engine for GRIP Copilot.

Learns user patterns over time to provide proactive, context-aware
assistance. Tracks routines, predicts needs, and generates deliverables
on demand.

Patterns learned:
- Work hours and productivity cycles
- Common task sequences (e.g., always runs tests before PR)
- Preferred tools and modes per task type
- Review patterns and feedback preferences
- Commit frequency and sprint rhythms

Usage:
    from aide.adaptive_learning import AdaptiveLearner
    learner = AdaptiveLearner()
    learner.record_event("commit", {"type": "feat", "time": "09:30"})
    predictions = learner.predict_next_actions()
    routine = learner.get_daily_routine()
"""

import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DATA_DIR = CLAUDE_DIR / "data" / "aide"
LEARNING_FILE = DATA_DIR / "adaptive-learning.jsonl"
ROUTINE_FILE = DATA_DIR / "learned-routines.json"
PREDICTIONS_FILE = DATA_DIR / "predictions-cache.json"


@dataclass
class UserEvent:
    """A recorded user interaction event."""
    timestamp: str
    event_type: str  # commit, pr_create, pr_merge, mode_switch, skill_use, review, deploy
    context: dict = field(default_factory=dict)
    hour: int = 0
    day_of_week: int = 0  # 0=Monday, 6=Sunday
    session_id: str = ""


@dataclass
class LearnedRoutine:
    """A detected pattern in user behavior."""
    name: str
    description: str
    trigger: str  # time-based, sequence-based, context-based
    confidence: float  # 0-1
    pattern: dict = field(default_factory=dict)
    occurrences: int = 0
    last_seen: str = ""

    @property
    def is_reliable(self) -> bool:
        """A pattern needs 3+ occurrences and >0.6 confidence to be reliable."""
        return self.occurrences >= 3 and self.confidence > 0.6


@dataclass
class Prediction:
    """A predicted next action for the user."""
    action: str
    reason: str
    confidence: float
    suggested_command: str = ""
    context: dict = field(default_factory=dict)


class AdaptiveLearner:
    """Core adaptive learning engine."""

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or DATA_DIR
        self.learning_file = self.data_dir / "adaptive-learning.jsonl"
        self.routine_file = self.data_dir / "learned-routines.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._events_cache = None
        self._routines_cache = None

    def record_event(self, event_type: str, context: dict = None,
                     session_id: str = "") -> UserEvent:
        """Record a user interaction event for learning."""
        now = datetime.now(timezone.utc)
        event = UserEvent(
            timestamp=now.isoformat(),
            event_type=event_type,
            context=context or {},
            hour=now.hour,
            day_of_week=now.weekday(),
            session_id=session_id,
        )

        with open(self.learning_file, "a") as f:
            f.write(json.dumps(asdict(event)) + "\n")

        self._events_cache = None  # Invalidate cache
        return event

    def get_events(self, days: int = 30) -> list:
        """Load events from the last N days."""
        if self._events_cache is not None:
            return self._events_cache

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        events = []

        if not self.learning_file.exists():
            return events

        with open(self.learning_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("timestamp", "") >= cutoff:
                        events.append(UserEvent(**{
                            k: data.get(k, v.default if hasattr(v, 'default') else "")
                            for k, v in UserEvent.__dataclass_fields__.items()
                        }))
                except (json.JSONDecodeError, TypeError):
                    continue

        self._events_cache = events
        return events

    def detect_routines(self, days: int = 30) -> list:
        """Analyze events to detect recurring patterns."""
        events = self.get_events(days)
        if not events:
            return []

        routines = []

        # 1. Time-based patterns: what does the user do at certain hours?
        hour_actions = defaultdict(list)
        for event in events:
            hour_actions[event.hour].append(event.event_type)

        for hour, actions in hour_actions.items():
            counter = Counter(actions)
            total = len(actions)
            for action, count in counter.most_common(1):
                if count >= 3:
                    confidence = count / max(total, 1)
                    routines.append(LearnedRoutine(
                        name=f"{action}_at_{hour:02d}",
                        description=f"User typically does '{action}' around {hour:02d}:00",
                        trigger="time-based",
                        confidence=round(confidence, 2),
                        pattern={"hour": hour, "action": action},
                        occurrences=count,
                        last_seen=events[-1].timestamp if events else "",
                    ))

        # 2. Sequence patterns: what follows what?
        for i in range(len(events) - 1):
            pair = (events[i].event_type, events[i + 1].event_type)
            # Count this pair across all events
            pair_count = sum(
                1 for j in range(len(events) - 1)
                if (events[j].event_type, events[j + 1].event_type) == pair
            )
            if pair_count >= 3:
                total_starts = sum(1 for e in events if e.event_type == pair[0])
                confidence = pair_count / max(total_starts, 1)
                routine_name = f"{pair[0]}_then_{pair[1]}"
                if not any(r.name == routine_name for r in routines):
                    routines.append(LearnedRoutine(
                        name=routine_name,
                        description=f"After '{pair[0]}', user usually does '{pair[1]}'",
                        trigger="sequence-based",
                        confidence=round(min(confidence, 1.0), 2),
                        pattern={"before": pair[0], "after": pair[1]},
                        occurrences=pair_count,
                        last_seen=events[-1].timestamp if events else "",
                    ))

        # 3. Day-of-week patterns
        dow_actions = defaultdict(list)
        dow_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        for event in events:
            dow_actions[event.day_of_week].append(event.event_type)

        for dow, actions in dow_actions.items():
            counter = Counter(actions)
            for action, count in counter.most_common(1):
                if count >= 3:
                    confidence = count / max(len(actions), 1)
                    routines.append(LearnedRoutine(
                        name=f"{action}_on_{dow_names[dow].lower()}",
                        description=f"User focuses on '{action}' on {dow_names[dow]}s",
                        trigger="day-based",
                        confidence=round(confidence, 2),
                        pattern={"day": dow, "day_name": dow_names[dow], "action": action},
                        occurrences=count,
                        last_seen=events[-1].timestamp if events else "",
                    ))

        # 4. Mode preferences per task type
        mode_actions = defaultdict(list)
        for event in events:
            mode = event.context.get("mode", "")
            if mode:
                mode_actions[mode].append(event.event_type)

        for mode, actions in mode_actions.items():
            counter = Counter(actions)
            for action, count in counter.most_common(1):
                if count >= 3:
                    routines.append(LearnedRoutine(
                        name=f"{mode}_for_{action}",
                        description=f"User prefers '{mode}' mode when doing '{action}'",
                        trigger="context-based",
                        confidence=round(count / max(len(actions), 1), 2),
                        pattern={"mode": mode, "action": action},
                        occurrences=count,
                        last_seen=events[-1].timestamp if events else "",
                    ))

        # Sort by confidence
        routines.sort(key=lambda r: r.confidence, reverse=True)

        # Save routines
        self._save_routines(routines)
        self._routines_cache = routines

        return routines

    def predict_next_actions(self, current_event: str = None,
                             current_hour: int = None) -> list:
        """Predict what the user is likely to do next.

        Returns list of Predictions sorted by confidence.
        """
        routines = self._load_routines()
        if not routines:
            routines = self.detect_routines()

        now = datetime.now(timezone.utc)
        hour = current_hour if current_hour is not None else now.hour
        predictions = []

        for routine in routines:
            if not routine.is_reliable:
                continue

            # Time-based predictions
            if routine.trigger == "time-based":
                if routine.pattern.get("hour") == hour:
                    predictions.append(Prediction(
                        action=routine.pattern["action"],
                        reason=routine.description,
                        confidence=routine.confidence,
                        context={"trigger": "time", "hour": hour},
                    ))

            # Sequence-based predictions
            elif routine.trigger == "sequence-based" and current_event:
                if routine.pattern.get("before") == current_event:
                    predictions.append(Prediction(
                        action=routine.pattern["after"],
                        reason=routine.description,
                        confidence=routine.confidence,
                        context={"trigger": "sequence", "after": current_event},
                    ))

            # Day-based predictions
            elif routine.trigger == "day-based":
                if routine.pattern.get("day") == now.weekday():
                    predictions.append(Prediction(
                        action=routine.pattern["action"],
                        reason=routine.description,
                        confidence=routine.confidence,
                        context={"trigger": "day", "day": routine.pattern.get("day_name", "")},
                    ))

        # Sort by confidence
        predictions.sort(key=lambda p: p.confidence, reverse=True)

        # Deduplicate by action
        seen = set()
        unique = []
        for p in predictions:
            if p.action not in seen:
                seen.add(p.action)
                unique.append(p)

        return unique[:5]  # Top 5 predictions

    def get_daily_routine(self) -> dict:
        """Generate a structured daily routine from learned patterns.

        Returns dict with hourly slots and common activities.
        """
        events = self.get_events(days=30)
        if not events:
            return {"slots": [], "summary": "No data yet. Keep using GRIP to build your routine."}

        # Group by hour
        hour_data = defaultdict(lambda: {"actions": [], "modes": []})
        for event in events:
            hour_data[event.hour]["actions"].append(event.event_type)
            mode = event.context.get("mode", "")
            if mode:
                hour_data[event.hour]["modes"].append(mode)

        slots = []
        for hour in sorted(hour_data.keys()):
            data = hour_data[hour]
            action_counts = Counter(data["actions"])
            mode_counts = Counter(data["modes"])
            top_action = action_counts.most_common(1)[0] if action_counts else ("idle", 0)
            top_mode = mode_counts.most_common(1)[0] if mode_counts else ("none", 0)

            slots.append({
                "hour": f"{hour:02d}:00",
                "primary_activity": top_action[0],
                "activity_count": top_action[1],
                "preferred_mode": top_mode[0],
                "total_events": len(data["actions"]),
            })

        # Find peak hours
        peak_hour = max(slots, key=lambda s: s["total_events"])

        return {
            "slots": slots,
            "peak_hour": peak_hour["hour"],
            "peak_activity": peak_hour["primary_activity"],
            "total_events": sum(s["total_events"] for s in slots),
            "summary": f"Peak productivity at {peak_hour['hour']} ({peak_hour['primary_activity']}). "
                       f"{len(slots)} active hours tracked across {sum(s['total_events'] for s in slots)} events.",
        }

    def generate_deliverable_suggestions(self) -> list:
        """Based on learned patterns, suggest deliverables the user can create.

        Returns list of deliverable suggestions with context.
        """
        events = self.get_events(days=14)
        routines = self._load_routines() or self.detect_routines()
        suggestions = []

        # Analyze recent activity types
        recent_types = Counter(e.event_type for e in events[-20:] if events)

        # If many commits but no PRs, suggest PR
        if recent_types.get("commit", 0) > 3 and recent_types.get("pr_create", 0) == 0:
            suggestions.append({
                "deliverable": "Pull Request",
                "reason": f"{recent_types['commit']} commits without a PR — time to ship",
                "command": "/create-pr",
                "priority": "high",
            })

        # If many reviews but no articles, suggest writeup
        if recent_types.get("review", 0) > 2:
            suggestions.append({
                "deliverable": "Code Review Summary",
                "reason": f"{recent_types['review']} reviews done — synthesize learnings",
                "command": "/sprint-report",
                "priority": "medium",
            })

        # If end of week, suggest changelog
        now = datetime.now(timezone.utc)
        if now.weekday() >= 3:  # Thursday+
            suggestions.append({
                "deliverable": "Weekly Changelog",
                "reason": "End of week — generate changelog for stakeholders",
                "command": "python3 lib/changelog_generator.py --format slack",
                "priority": "medium",
            })

        # If many test events, suggest delivery report
        if recent_types.get("test", 0) > 5:
            suggestions.append({
                "deliverable": "Delivery Report",
                "reason": "Significant testing activity — create client-ready report",
                "command": "python3 lib/copilot/delivery_report.py",
                "priority": "medium",
            })

        # If sprint-related activity, suggest standup
        if recent_types.get("sprint", 0) > 0 or recent_types.get("commit", 0) > 5:
            suggestions.append({
                "deliverable": "Standup Summary",
                "reason": "Active sprint work — generate standup update",
                "command": "/standup",
                "priority": "low",
            })

        # Always suggest health check
        suggestions.append({
            "deliverable": "Project Health Dashboard",
            "reason": "Regular health monitoring",
            "command": "python3 lib/project_health.py summary",
            "priority": "low",
        })

        return sorted(suggestions, key=lambda s: {"high": 0, "medium": 1, "low": 2}[s["priority"]])

    def format_status(self) -> str:
        """Format current learning status as text."""
        events = self.get_events(days=30)
        routines = self._load_routines() or []
        reliable = [r for r in routines if r.is_reliable]
        predictions = self.predict_next_actions()

        lines = [
            f"Adaptive Learning Status",
            f"  Events tracked: {len(events)} (30 days)",
            f"  Patterns detected: {len(routines)} ({len(reliable)} reliable)",
            "",
        ]

        if reliable:
            lines.append("Learned Routines:")
            for r in reliable[:5]:
                lines.append(f"  [{r.confidence:.0%}] {r.description}")
            lines.append("")

        if predictions:
            lines.append("Current Predictions:")
            for p in predictions[:3]:
                lines.append(f"  [{p.confidence:.0%}] Next: {p.action} — {p.reason}")
            lines.append("")

        suggestions = self.generate_deliverable_suggestions()
        if suggestions:
            lines.append("Suggested Deliverables:")
            for s in suggestions[:3]:
                lines.append(f"  [{s['priority'].upper()}] {s['deliverable']} — {s['reason']}")

        return "\n".join(lines)

    def format_slack(self) -> str:
        """Format status for Slack mrkdwn."""
        routines = self._load_routines() or []
        reliable = [r for r in routines if r.is_reliable]
        predictions = self.predict_next_actions()

        lines = ["*Adaptive Copilot Learning*", ""]

        if reliable:
            lines.append("*Learned Routines:*")
            for r in reliable[:5]:
                lines.append(f"  - [{r.confidence:.0%}] {r.description}")
            lines.append("")

        if predictions:
            lines.append("*Predictions:*")
            for p in predictions[:3]:
                lines.append(f"  - [{p.confidence:.0%}] {p.action}: {p.reason}")

        return "\n".join(lines)

    def _save_routines(self, routines: list):
        """Persist routines to disk."""
        data = [asdict(r) for r in routines]
        with open(self.routine_file, "w") as f:
            json.dump(data, f, indent=2)

    def _load_routines(self) -> list:
        """Load routines from disk."""
        if self._routines_cache is not None:
            return self._routines_cache

        if not self.routine_file.exists():
            return []

        try:
            with open(self.routine_file) as f:
                data = json.load(f)
            routines = [LearnedRoutine(**d) for d in data]
            self._routines_cache = routines
            return routines
        except (json.JSONDecodeError, TypeError):
            return []


def main():
    """CLI entry point."""
    learner = AdaptiveLearner()

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        print(learner.format_status())
    elif cmd == "routines":
        routines = learner.detect_routines()
        for r in routines:
            reliable = "R" if r.is_reliable else " "
            print(f"  [{reliable}] [{r.confidence:.0%}] {r.name}: {r.description}")
    elif cmd == "predict":
        event = sys.argv[2] if len(sys.argv) > 2 else None
        predictions = learner.predict_next_actions(current_event=event)
        for p in predictions:
            print(f"  [{p.confidence:.0%}] {p.action}: {p.reason}")
    elif cmd == "routine":
        routine = learner.get_daily_routine()
        print(f"Daily Routine ({routine['summary']})")
        for slot in routine.get("slots", []):
            print(f"  {slot['hour']} - {slot['primary_activity']} ({slot['total_events']} events)")
    elif cmd == "deliverables":
        suggestions = learner.generate_deliverable_suggestions()
        for s in suggestions:
            print(f"  [{s['priority'].upper()}] {s['deliverable']}: {s['reason']}")
    elif cmd == "record":
        if len(sys.argv) < 3:
            print("Usage: adaptive_learning.py record <event_type> [key=value ...]")
            sys.exit(1)
        event_type = sys.argv[2]
        context = {}
        for arg in sys.argv[3:]:
            if "=" in arg:
                k, v = arg.split("=", 1)
                context[k] = v
        event = learner.record_event(event_type, context)
        print(f"Recorded: {event.event_type} at {event.timestamp}")
    elif cmd == "slack":
        print(learner.format_slack())
    else:
        print(f"Unknown command: {cmd}")
        print("Commands: status, routines, predict, routine, deliverables, record, slack")
        sys.exit(1)


if __name__ == "__main__":
    main()
