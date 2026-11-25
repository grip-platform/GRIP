#!/usr/bin/env python3
"""
approach_history.py -- Decision history and visualisation for approach gates.

Records approach gate decisions to JSONL, supports retroactive outcome
tagging, and generates ASCII reports with trend analysis.

Usage:
    from lib.approach_history import ApproachHistory, ApproachDecision

    history = ApproachHistory()
    history.record_decision(
        task="Refactor auth system",
        approaches=[
            {"name": "monolith", "score": 0.4, "chosen": False},
            {"name": "microservice", "score": 0.9, "chosen": True},
        ],
        chosen="microservice",
        reasoning="Better isolation for auth concerns",
    )
    history.record_outcome(task_hash="abc123", outcome="positive")
    print(history.format_report())
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
DEFAULT_HISTORY_FILE = "approach-history.jsonl"


@dataclass
class ApproachDecision:
    """A single recorded approach gate decision."""

    task_hash: str
    task_summary: str
    approaches: list[dict]  # [{"name": str, "score": float, "chosen": bool}, ...]
    chosen_approach: str
    reasoning: str
    timestamp: str  # ISO 8601
    outcome: Optional[str] = None  # "positive", "negative", or None (pending)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ApproachDecision:
        return cls(
            task_hash=data["task_hash"],
            task_summary=data["task_summary"],
            approaches=data["approaches"],
            chosen_approach=data["chosen_approach"],
            reasoning=data["reasoning"],
            timestamp=data["timestamp"],
            outcome=data.get("outcome"),
        )


def _task_hash(task: str) -> str:
    """Deterministic hash for a task description."""
    return hashlib.sha256(task.encode("utf-8")).hexdigest()[:12]


class ApproachHistory:
    """Tracks approach gate decisions over time with outcome analysis."""

    def __init__(self, data_dir: Optional[Path] = None):
        self._data_dir = data_dir or (CLAUDE_DIR / "data")
        self._history_file = self._data_dir / DEFAULT_HISTORY_FILE
        self._decisions: list[ApproachDecision] | None = None

    @property
    def history_file(self) -> Path:
        return self._history_file

    def _ensure_dir(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def _invalidate_cache(self) -> None:
        self._decisions = None

    def record_decision(
        self,
        task: str,
        approaches: list[dict],
        chosen: str,
        reasoning: str,
    ) -> ApproachDecision:
        """Append a new decision to the JSONL history file.

        Args:
            task: Human-readable task description.
            approaches: List of dicts, each with "name", "score", "chosen".
            chosen: Name of the chosen approach.
            reasoning: Why this approach was selected.

        Returns:
            The recorded ApproachDecision.
        """
        self._ensure_dir()

        decision = ApproachDecision(
            task_hash=_task_hash(task),
            task_summary=task,
            approaches=approaches,
            chosen_approach=chosen,
            reasoning=reasoning,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        with open(self._history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(decision.to_dict()) + "\n")

        self._invalidate_cache()
        return decision

    def load_history(self) -> list[ApproachDecision]:
        """Load all decisions from the JSONL file. Cached after first load."""
        if self._decisions is not None:
            return self._decisions

        if not self._history_file.exists():
            self._decisions = []
            return self._decisions

        decisions: list[ApproachDecision] = []
        with open(self._history_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    decisions.append(ApproachDecision.from_dict(data))
                except (json.JSONDecodeError, KeyError):
                    continue  # Skip malformed lines gracefully

        self._decisions = decisions
        return self._decisions

    def record_outcome(self, task_hash: str, outcome: str) -> bool:
        """Retroactively record the outcome for a decision.

        Rewrites the JSONL file with the outcome applied to the
        most recent decision matching task_hash.

        Args:
            task_hash: The hash identifying the decision.
            outcome: "positive" or "negative".

        Returns:
            True if a matching decision was found and updated.
        """
        decisions = self.load_history()
        found = False

        # Find the most recent matching decision (iterate in reverse)
        for decision in reversed(decisions):
            if decision.task_hash == task_hash:
                decision.outcome = outcome
                found = True
                break

        if found:
            self._ensure_dir()
            with open(self._history_file, "w", encoding="utf-8") as f:
                for d in decisions:
                    f.write(json.dumps(d.to_dict()) + "\n")
            self._invalidate_cache()

        return found

    def win_rate(self) -> float:
        """Percentage of decisions with positive outcomes.

        Only considers decisions that have an outcome recorded.
        Returns 0.0 if no outcomes exist.
        """
        decisions = self.load_history()
        rated = [d for d in decisions if d.outcome is not None]
        if not rated:
            return 0.0
        positive = sum(1 for d in rated if d.outcome == "positive")
        return (positive / len(rated)) * 100.0

    def approach_frequency(self) -> dict[str, int]:
        """Count how often each approach name was chosen.

        Returns dict mapping approach name to count, sorted descending.
        """
        decisions = self.load_history()
        freq: dict[str, int] = {}
        for d in decisions:
            name = d.chosen_approach
            freq[name] = freq.get(name, 0) + 1
        return dict(sorted(freq.items(), key=lambda kv: kv[1], reverse=True))

    def decision_quality_trend(self, window: int = 10) -> str:
        """Analyse recent outcome trend over a sliding window.

        Compares win rate of the most recent `window` rated decisions
        against the preceding `window`. Returns "improving", "stable",
        or "declining".
        """
        decisions = self.load_history()
        rated = [d for d in decisions if d.outcome is not None]

        if len(rated) < 2:
            return "stable"

        # Split into recent and prior windows
        recent = rated[-window:]
        prior_end = max(0, len(rated) - window)
        prior_start = max(0, prior_end - window)
        prior = rated[prior_start:prior_end]

        if not prior:
            return "stable"

        recent_rate = sum(1 for d in recent if d.outcome == "positive") / len(recent)
        prior_rate = sum(1 for d in prior if d.outcome == "positive") / len(prior)

        diff = recent_rate - prior_rate
        if diff > 0.1:
            return "improving"
        elif diff < -0.1:
            return "declining"
        return "stable"

    def _sparkline(self, values: list[float], width: int = 20) -> str:
        """Generate a sparkline string from a list of values (0.0-1.0)."""
        blocks = " _.:oO#@"
        if not values:
            return ""
        result = []
        for v in values:
            idx = int(min(v, 1.0) * (len(blocks) - 1))
            result.append(blocks[idx])
        return "".join(result)

    def _bar_chart(self, data: dict[str, int], width: int = 30) -> str:
        """Generate an ASCII horizontal bar chart."""
        if not data:
            return "  (no data)\n"
        max_val = max(data.values())
        max_label = max(len(k) for k in data)
        lines = []
        for name, count in data.items():
            bar_len = int((count / max_val) * width) if max_val > 0 else 0
            bar = "#" * bar_len
            lines.append(f"  {name:<{max_label}} |{bar} {count}")
        return "\n".join(lines) + "\n"

    def format_report(self) -> str:
        """Generate a full ASCII report of approach gate history.

        Sections:
        - Summary stats
        - Recent decisions table
        - Win rate sparkline
        - Most-chosen approaches bar chart
        - Hindsight: decisions marked negative
        """
        decisions = self.load_history()

        if not decisions:
            return (
                "=== Approach Gate History ===\n"
                "\n"
                "No decisions recorded yet.\n"
                "Use record_decision() to start tracking.\n"
            )

        rated = [d for d in decisions if d.outcome is not None]
        negative = [d for d in decisions if d.outcome == "negative"]
        trend = self.decision_quality_trend()
        trend_arrow = {"improving": "^", "stable": "=", "declining": "v"}[trend]

        lines = [
            "=== Approach Gate History ===",
            "",
            f"Total decisions: {len(decisions)}",
            f"Rated outcomes:  {len(rated)}",
            f"Win rate:        {self.win_rate():.1f}%",
            f"Trend:           {trend} {trend_arrow}",
            "",
        ]

        # Recent decisions table
        lines.append("--- Recent Decisions (last 10) ---")
        recent = decisions[-10:]
        lines.append(f"  {'Hash':<14} {'Chosen':<20} {'Outcome':<10} {'Task'}")
        lines.append(f"  {'----':<14} {'------':<20} {'-------':<10} {'----'}")
        for d in recent:
            outcome_str = d.outcome or "pending"
            summary = d.task_summary[:40]
            lines.append(
                f"  {d.task_hash:<14} {d.chosen_approach:<20} {outcome_str:<10} {summary}"
            )
        lines.append("")

        # Win rate sparkline (rolling window of 5)
        if len(rated) >= 2:
            lines.append("--- Win Rate Trend ---")
            window = 5
            rolling: list[float] = []
            for i in range(len(rated)):
                start = max(0, i - window + 1)
                chunk = rated[start : i + 1]
                rate = sum(1 for d in chunk if d.outcome == "positive") / len(chunk)
                rolling.append(rate)
            spark = self._sparkline(rolling)
            lines.append(f"  [{spark}]")
            lines.append("")

        # Approach frequency bar chart
        freq = self.approach_frequency()
        if freq:
            lines.append("--- Most Chosen Approaches ---")
            lines.append(self._bar_chart(freq))
            lines.append("")

        # Hindsight: negative outcomes
        if negative:
            lines.append("--- Hindsight: Negative Outcomes ---")
            for d in negative:
                ts_short = d.timestamp[:10] if len(d.timestamp) >= 10 else d.timestamp
                lines.append(
                    f"  [{ts_short}] {d.task_hash} chose '{d.chosen_approach}'"
                )
                lines.append(f"    Task: {d.task_summary[:60]}")
                lines.append(f"    Reasoning: {d.reasoning[:60]}")
            lines.append("")

        return "\n".join(lines)

    def format_compact(self) -> str:
        """Single-line compact summary for statusline integration.

        Format: gate:12dec 83%win ^
        """
        decisions = self.load_history()
        if not decisions:
            return "gate:0dec"

        trend = self.decision_quality_trend()
        trend_sym = {"improving": "^", "stable": "=", "declining": "v"}[trend]
        rate = self.win_rate()
        rated = [d for d in decisions if d.outcome is not None]

        if not rated:
            return f"gate:{len(decisions)}dec"

        return f"gate:{len(decisions)}dec {rate:.0f}%win {trend_sym}"


def main() -> None:
    """CLI entry point for approach history inspection."""
    import sys

    history = ApproachHistory()

    if len(sys.argv) < 2 or sys.argv[1] in ("report", "--help", "-h"):
        print(history.format_report())
        return

    cmd = sys.argv[1]

    if cmd == "compact":
        print(history.format_compact())
    elif cmd == "outcome" and len(sys.argv) >= 4:
        task_hash = sys.argv[2]
        outcome = sys.argv[3]
        if outcome not in ("positive", "negative"):
            print("Outcome must be 'positive' or 'negative'", file=sys.stderr)
            sys.exit(1)
        if history.record_outcome(task_hash, outcome):
            print(f"Recorded {outcome} outcome for {task_hash}")
        else:
            print(f"No decision found with hash {task_hash}", file=sys.stderr)
            sys.exit(1)
    elif cmd == "frequency":
        freq = history.approach_frequency()
        if not freq:
            print("No decisions recorded.")
            return
        for name, count in freq.items():
            print(f"  {name}: {count}")
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print("Usage: approach_history.py [report|compact|frequency|outcome HASH positive|negative]")
        sys.exit(1)


if __name__ == "__main__":
    main()
