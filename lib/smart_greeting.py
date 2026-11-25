#!/usr/bin/env python3
"""GRIP Smart Greeting -- contextual session startup message.

Generates time-appropriate, data-rich greetings using available GRIP
analytics: achievements, skill recommendations, convergence weather,
and usage streaks.

Usage:
    python3 lib/smart_greeting.py          # Print greeting
    python3 lib/smart_greeting.py --json   # Machine-readable output
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List, Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Greeting:
    """Contextual session greeting."""

    salutation: str
    achievements_msg: Optional[str] = None
    suggestions: List[str] = field(default_factory=list)
    forecast: str = "No forecast data"
    streak_msg: Optional[str] = None
    time_context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Safe import helper
# ---------------------------------------------------------------------------

def _safe_import(module_name: str, func_name: str) -> Optional[Callable]:
    """Import a function from a GRIP module with graceful fallback.

    Adds the lib directory to sys.path if needed, then attempts to import
    ``func_name`` from ``module_name``.  Returns None on any failure so
    callers can degrade gracefully.
    """
    try:
        lib_dir = str(Path(__file__).resolve().parent)
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)
        mod = importlib.import_module(module_name)
        return getattr(mod, func_name, None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _get_salutation(now: Optional[datetime] = None) -> str:
    """Return a time-appropriate salutation."""
    if now is None:
        now = datetime.now()
    hour = now.hour
    if 0 <= hour < 5:
        return "Late night session"
    elif 5 <= hour < 12:
        return "Good morning"
    elif 12 <= hour < 17:
        return "Good afternoon"
    else:
        return "Good evening"


def _get_time_context(now: Optional[datetime] = None) -> str:
    """Return day-of-week and time period string."""
    if now is None:
        now = datetime.now()
    day = now.strftime("%A")
    hour = now.hour
    if 0 <= hour < 5:
        period = "late night"
    elif 5 <= hour < 12:
        period = "morning"
    elif 12 <= hour < 17:
        period = "afternoon"
    else:
        period = "evening"
    return f"{day} {period}"


# ---------------------------------------------------------------------------
# Data gatherers
# ---------------------------------------------------------------------------

def _get_achievements_msg(claude_dir: str) -> Optional[str]:
    """Check for recently unlocked badges and return a summary message."""
    try:
        load_store = _safe_import("achievements", "load_store")
        all_badges_fn = _safe_import("achievements", "all_badges")
        if load_store is None or all_badges_fn is None:
            return None

        cache_dir = os.path.join(claude_dir, "cache")
        store = load_store(cache_dir)
        badges = all_badges_fn(store)

        unlocked = [b for b in badges if b.is_unlocked]
        if not unlocked:
            return None

        # Find badges unlocked in the last 24 hours
        now = datetime.now()
        recent = []
        for b in unlocked:
            if b.unlocked_at:
                try:
                    unlock_time = datetime.fromisoformat(b.unlocked_at)
                    delta = now - unlock_time
                    if delta.total_seconds() < 86400:
                        recent.append(b.name)
                except (ValueError, TypeError):
                    pass

        total = len(badges)
        unlocked_count = len(unlocked)

        if recent:
            names = ", ".join(recent[:3])
            suffix = f" (+{len(recent) - 3} more)" if len(recent) > 3 else ""
            return f"New badge{'s' if len(recent) != 1 else ''}: {names}{suffix} | {unlocked_count}/{total} unlocked"
        return f"{unlocked_count}/{total} badges unlocked"
    except Exception:
        return None


def _get_suggestions(claude_dir: str) -> List[str]:
    """Get top 3 skill recommendations based on recent session context."""
    try:
        recommend_fn = _safe_import("skill_recommender", "recommend")
        if recommend_fn is None:
            return []

        skills_dir = os.path.join(claude_dir, "skills")
        # Recommend skills for a generic "session startup" context
        recs = recommend_fn(
            "session startup productivity workflow",
            top_n=3,
            skills_dir=skills_dir,
        )
        return [r.skill_name for r in recs]
    except Exception:
        return []


def _get_forecast(claude_dir: str) -> str:
    """Get convergence or codebase weather forecast."""
    # Try converge_weather first, fall back to codebase_weather
    for module_name in ("converge_weather", "codebase_weather"):
        try:
            forecast_fn = _safe_import(module_name, "forecast")
            if forecast_fn is None:
                # Try alternate function names
                forecast_fn = _safe_import(module_name, "generate_forecast")
            if forecast_fn is None:
                continue

            result = forecast_fn()
            if result is None:
                continue

            # Extract a summary string from whatever the forecast returns
            if isinstance(result, str):
                # Truncate to one line
                first_line = result.strip().split("\n")[0]
                return first_line[:120]
            elif isinstance(result, dict):
                summary = result.get("summary") or result.get("condition") or result.get("forecast")
                if summary:
                    return str(summary)[:120]
            elif hasattr(result, "summary"):
                return str(result.summary)[:120]
            elif hasattr(result, "condition"):
                return str(result.condition)[:120]
        except Exception:
            continue

    return "No forecast data"


def _get_streak_msg(claude_dir: str) -> Optional[str]:
    """Get current usage streak from achievements data."""
    try:
        gather_stats = _safe_import("achievements", "gather_all_stats")
        if gather_stats is None:
            return None

        stats = gather_stats(claude_dir)
        streak = stats.get("usage_streak", 0)
        sessions = stats.get("sessions", 0)

        if streak <= 0 and sessions <= 0:
            return None
        parts = []
        if streak > 0:
            day_word = "day" if streak == 1 else "days"
            parts.append(f"{streak}-{day_word} streak")
        if sessions > 0:
            parts.append(f"{sessions} sessions")
        return " | ".join(parts)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def generate_greeting(claude_dir: Optional[str] = None) -> Greeting:
    """Generate a contextual session greeting.

    Args:
        claude_dir: Path to .claude directory. Defaults to ~/.claude.

    Returns:
        Greeting dataclass with all available context filled in.
    """
    if claude_dir is None:
        claude_dir = os.path.expanduser("~/.claude")

    now = datetime.now()

    return Greeting(
        salutation=_get_salutation(now),
        achievements_msg=_get_achievements_msg(claude_dir),
        suggestions=_get_suggestions(claude_dir),
        forecast=_get_forecast(claude_dir),
        streak_msg=_get_streak_msg(claude_dir),
        time_context=_get_time_context(now),
    )


def format_greeting(greeting: Greeting) -> str:
    """Format a Greeting as compact 3-5 line output.

    Line 1: salutation + time context
    Line 2: streak + achievements (if any)
    Line 3: forecast
    Line 4: suggested skills (if any)
    """
    lines: list[str] = []

    # Line 1: salutation + time context
    line1 = greeting.salutation
    if greeting.time_context:
        line1 += f" ({greeting.time_context})"
    lines.append(line1)

    # Line 2: streak + achievements (if any)
    line2_parts: list[str] = []
    if greeting.streak_msg:
        line2_parts.append(greeting.streak_msg)
    if greeting.achievements_msg:
        line2_parts.append(greeting.achievements_msg)
    if line2_parts:
        lines.append(" | ".join(line2_parts))

    # Line 3: forecast
    lines.append(f"Forecast: {greeting.forecast}")

    # Line 4: suggested skills (if any)
    if greeting.suggestions:
        skills_str = ", ".join(greeting.suggestions)
        lines.append(f"Try: {skills_str}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="GRIP Smart Greeting -- contextual session startup message"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable JSON output",
    )
    parser.add_argument(
        "--claude-dir",
        default=None,
        help="Path to .claude directory (default: ~/.claude)",
    )
    args = parser.parse_args(argv)

    claude_dir = args.claude_dir or os.path.expanduser("~/.claude")
    greeting = generate_greeting(claude_dir)

    if args.json:
        print(json.dumps(greeting.to_dict(), indent=2))
    else:
        print(format_greeting(greeting))

    return 0


if __name__ == "__main__":
    sys.exit(main())
