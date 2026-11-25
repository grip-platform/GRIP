#!/usr/bin/env python3
"""
Skill Usage Analytics for GRIP

Tracks which skills are being invoked and provides usage statistics.
Helps identify underused skills that may need deprecation.

Usage:
    python3 skill-analytics.py log <skill_name>   # Log a skill invocation
    python3 skill-analytics.py stats              # Show usage statistics
    python3 skill-analytics.py top [N]            # Show top N skills
    python3 skill-analytics.py unused [days]      # Show unused skills
    python3 skill-analytics.py report             # Full usage report
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from json_utils import safe_load_jsonl

CLAUDE_DIR = Path.home() / ".claude"
ANALYTICS_FILE = CLAUDE_DIR / "skill-usage.jsonl"
SKILLS_DIR = CLAUDE_DIR / "skills"


def log_invocation(skill_name: str, source: str = "manual") -> None:
    """Log a skill invocation with timestamp."""
    entry = {
        "timestamp": int(datetime.now().timestamp() * 1000),
        "skill": skill_name,
        "source": source,  # "manual", "hook", "auto"
        "date": datetime.now().isoformat()
    }

    try:
        from locked_append import locked_append as _la
        _la(str(ANALYTICS_FILE), json.dumps(entry))
    except ImportError:
        with open(ANALYTICS_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")

    print(f"[SKILL-ANALYTICS] Logged: {skill_name}")


def load_usage_data() -> list:
    """Load all usage entries from the analytics file."""
    return safe_load_jsonl(ANALYTICS_FILE)


def get_all_skills() -> set:
    """Get all available skill names from the skills directory."""
    skills = set()

    if SKILLS_DIR.exists():
        for skill_dir in SKILLS_DIR.iterdir():
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                skills.add(skill_dir.name)

    return skills


def show_stats() -> None:
    """Show overall usage statistics."""
    entries = load_usage_data()
    all_skills = get_all_skills()

    if not entries:
        print("No usage data found. Skills will be tracked as they are used.")
        return

    # Count by skill
    skill_counts = defaultdict(int)
    for entry in entries:
        skill_counts[entry["skill"]] += 1

    # Calculate stats
    total_invocations = len(entries)
    unique_skills_used = len(skill_counts)
    total_skills = len(all_skills)
    used_skills = set(skill_counts.keys())
    unused_skills = all_skills - used_skills

    # Time range
    timestamps = [e["timestamp"] for e in entries]
    first_use = datetime.fromtimestamp(min(timestamps) / 1000)
    last_use = datetime.fromtimestamp(max(timestamps) / 1000)

    print("=" * 50)
    print("GRIP SKILL USAGE STATISTICS")
    print("=" * 50)
    print(f"Total Invocations:     {total_invocations}")
    print(f"Unique Skills Used:    {unique_skills_used}")
    print(f"Total Skills Available:{total_skills}")
    print(f"Skills Never Used:     {len(unused_skills)}")
    print(f"Usage Rate:            {unique_skills_used}/{total_skills} ({100*unique_skills_used/total_skills:.1f}%)")
    print(f"Tracking Since:        {first_use.strftime('%Y-%m-%d')}")
    print(f"Last Invocation:       {last_use.strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)


def show_top(n: int = 10) -> None:
    """Show top N most-used skills."""
    entries = load_usage_data()

    if not entries:
        print("No usage data found.")
        return

    skill_counts = defaultdict(int)
    for entry in entries:
        skill_counts[entry["skill"]] += 1

    sorted_skills = sorted(skill_counts.items(), key=lambda x: x[1], reverse=True)

    print(f"\nTOP {n} MOST USED SKILLS")
    print("-" * 40)
    print(f"{'Skill':<30} {'Count':>8}")
    print("-" * 40)

    for skill, count in sorted_skills[:n]:
        print(f"{skill:<30} {count:>8}")

    print("-" * 40)


def show_unused(days: int = 30) -> None:
    """Show skills unused in the last N days."""
    entries = load_usage_data()
    all_skills = get_all_skills()

    cutoff = datetime.now() - timedelta(days=days)
    cutoff_ts = cutoff.timestamp() * 1000

    # Skills used within the time window
    recent_skills = set()
    for entry in entries:
        if entry["timestamp"] >= cutoff_ts:
            recent_skills.add(entry["skill"])

    unused = all_skills - recent_skills

    print(f"\nSKILLS NOT USED IN LAST {days} DAYS")
    print("-" * 40)

    if not unused:
        print("All skills have been used recently!")
        return

    # Sort alphabetically
    for skill in sorted(unused):
        print(f"  - {skill}")

    print("-" * 40)
    print(f"Total unused: {len(unused)} / {len(all_skills)}")

    # Suggest deprecation candidates
    if len(unused) > 10:
        print("\n⚠️  Consider reviewing these skills for deprecation.")


def show_report() -> None:
    """Show full usage report."""
    entries = load_usage_data()
    all_skills = get_all_skills()

    print("=" * 60)
    print("GRIP SKILL USAGE REPORT")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Stats summary
    show_stats()

    # Top skills
    print()
    show_top(15)

    # Unused skills
    print()
    show_unused(30)

    # Usage by source
    print("\nUSAGE BY SOURCE")
    print("-" * 40)

    source_counts = defaultdict(int)
    for entry in entries:
        source_counts[entry.get("source", "unknown")] += 1

    for source, count in sorted(source_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {source:<20} {count:>8}")

    # Recent trend (last 7 days vs previous 7)
    print("\nRECENT TREND (7-DAY COMPARISON)")
    print("-" * 40)

    now = datetime.now()
    week_ago = (now - timedelta(days=7)).timestamp() * 1000
    two_weeks_ago = (now - timedelta(days=14)).timestamp() * 1000

    this_week = sum(1 for e in entries if e["timestamp"] >= week_ago)
    last_week = sum(1 for e in entries if two_weeks_ago <= e["timestamp"] < week_ago)

    if last_week > 0:
        change = ((this_week - last_week) / last_week) * 100
        trend = "📈" if change > 0 else "📉" if change < 0 else "➡️"
        print(f"  This week:  {this_week}")
        print(f"  Last week:  {last_week}")
        print(f"  Trend:      {trend} {change:+.1f}%")
    else:
        print(f"  This week:  {this_week}")
        print(f"  Last week:  (no data)")

    print("=" * 60)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "log":
        if len(sys.argv) < 3:
            print("Usage: skill-analytics.py log <skill_name> [source]")
            sys.exit(1)
        skill_name = sys.argv[2]
        source = sys.argv[3] if len(sys.argv) > 3 else "manual"
        log_invocation(skill_name, source)

    elif command == "stats":
        show_stats()

    elif command == "top":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        show_top(n)

    elif command == "unused":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        show_unused(days)

    elif command == "report":
        show_report()

    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
