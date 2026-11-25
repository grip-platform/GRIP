#!/usr/bin/env python3
"""
Improvement Analyzer - Meta-learning about self-improvement patterns.

Tracks and analyzes system improvements to reveal:
1. Component heat map - which parts need most fixes?
2. Trigger analysis - what causes improvements?
3. Improvement velocity - how fast is the system evolving?

Usage:
    python3 improvement-analyzer.py [summary|heatmap|triggers|recent]
"""

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from json_utils import safe_load_jsonl

CLAUDE_DIR = Path.home() / ".claude"
LOG_FILE = CLAUDE_DIR / "improvement-log.jsonl"


def load_improvements() -> list:
    """Load all improvements from JSONL log."""
    return safe_load_jsonl(LOG_FILE)


def summary():
    """Print summary statistics."""
    improvements = load_improvements()
    if not improvements:
        print("No improvements logged yet.")
        return

    print(f"=== GRIP Self-Improvement Summary ===\n")
    print(f"Total improvements: {len(improvements)}")

    # Type breakdown
    types = Counter(i.get("type", "unknown") for i in improvements)
    print(f"\nBy type:")
    for t, count in types.most_common():
        print(f"  {t}: {count}")

    # Trigger breakdown
    triggers = Counter(i.get("trigger", "unknown") for i in improvements)
    print(f"\nBy trigger:")
    for t, count in triggers.most_common():
        print(f"  {t}: {count}")

    # Sessions
    sessions = set(i.get("session", "unknown")[:8] for i in improvements)
    print(f"\nAcross {len(sessions)} session(s)")


def heatmap():
    """Show which components get improved most."""
    improvements = load_improvements()
    if not improvements:
        print("No improvements logged yet.")
        return

    components = Counter(i.get("component", "unknown") for i in improvements)

    print("=== Component Heat Map ===\n")
    print("Components improved most often (potential design smells):\n")

    for comp, count in components.most_common(10):
        bar = "█" * count
        print(f"  {comp}: {bar} ({count})")


def triggers():
    """Analyze what triggers improvements."""
    improvements = load_improvements()
    if not improvements:
        print("No improvements logged yet.")
        return

    trigger_counts = Counter(i.get("trigger", "unknown") for i in improvements)

    print("=== Improvement Triggers ===\n")

    for trigger, count in trigger_counts.most_common():
        pct = (count / len(improvements)) * 100
        print(f"  {trigger}: {count} ({pct:.0f}%)")

    # Insight
    top_trigger = trigger_counts.most_common(1)[0][0] if trigger_counts else None
    if top_trigger == "friction_encountered":
        print("\n→ Most improvements come from hitting actual friction.")
        print("  This is healthy - system improves through use.")
    elif top_trigger == "proactive":
        print("\n→ Most improvements are proactive.")
        print("  Consider: are you over-engineering?")


def recent(n: int = 5):
    """Show recent improvements."""
    improvements = load_improvements()
    if not improvements:
        print("No improvements logged yet.")
        return

    print(f"=== Last {n} Improvements ===\n")

    # Sort by timestamp (most recent first)
    sorted_imps = sorted(
        improvements,
        key=lambda x: x.get("timestamp", ""),
        reverse=True
    )[:n]

    for imp in sorted_imps:
        ts = imp.get("timestamp", "unknown")[:16]
        typ = imp.get("type", "?")
        comp = imp.get("component", "?").split("/")[-1]
        desc = imp.get("description", "")[:60]
        print(f"  [{ts}] {typ}: {comp}")
        print(f"    {desc}")
        print()


def get_refactor_candidates(threshold: int = 3) -> list:
    """Return components improved >= threshold times (design smell detection).

    Components that need frequent improvement may violate Single Responsibility.
    This feeds into the recursive learning loop.
    """
    improvements = load_improvements()
    if not improvements:
        return []

    components = Counter(i.get("component", "unknown") for i in improvements)
    return [comp for comp, count in components.items() if count >= threshold]


def check_and_signal(threshold: int = 3) -> bool:
    """Check for refactor candidates and emit signal if found.

    Returns True if signal was emitted, False otherwise.
    This is called by the learning loop to detect design smells.
    """
    candidates = get_refactor_candidates(threshold=threshold)
    if candidates:
        print(f"[IMPROVEMENT HEATMAP] Components need refactor: {', '.join(candidates)}")
        return True
    return False


def log_improvement(
    improvement_type: str,
    component: str,
    description: str,
    trigger: str = "manual",
    session: str = None,
    **kwargs
):
    """Log a new improvement.

    Args:
        improvement_type: Type of improvement (skill_created, rule_added, etc.)
        component: Component being improved (e.g., lib/learning.sh)
        description: What was improved
        trigger: What caused the improvement (friction_encountered, proactive, auto)
        session: Optional session ID for tracking
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": improvement_type,
        "component": component,
        "description": description,
        "trigger": trigger,
        **kwargs
    }

    if session:
        entry["session"] = session

    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    print(f"Logged: {improvement_type} to {component}")

    # Check if this triggers a heatmap signal
    check_and_signal()


def main():
    if len(sys.argv) < 2:
        summary()
        return

    cmd = sys.argv[1]

    if cmd == "summary":
        summary()
    elif cmd == "heatmap":
        heatmap()
    elif cmd == "triggers":
        triggers()
    elif cmd == "recent":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        recent(n)
    elif cmd == "check":
        # Check for refactor candidates and emit signal
        if not check_and_signal():
            print("No components need refactoring (threshold: 3+ improvements)")
    elif cmd == "candidates":
        # List refactor candidates
        candidates = get_refactor_candidates()
        if candidates:
            print("=== Refactor Candidates ===\n")
            print("Components improved 3+ times:\n")
            for comp in candidates:
                print(f"  - {comp}")
        else:
            print("No refactor candidates found.")
    elif cmd == "log":
        # Log a new improvement: log <type> <component> <description> [trigger]
        if len(sys.argv) < 5:
            print("Usage: improvement-analyzer.py log <type> <component> <description> [trigger]")
            print("  type: skill_created, rule_added, bug_fixed, refactor, etc.")
            print("  component: file/skill being improved")
            print("  description: what was improved")
            print("  trigger: friction_encountered, proactive, auto (default: manual)")
            sys.exit(1)
        imp_type = sys.argv[2]
        component = sys.argv[3]
        description = sys.argv[4]
        trigger = sys.argv[5] if len(sys.argv) > 5 else "manual"
        log_improvement(imp_type, component, description, trigger)
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: improvement-analyzer.py [summary|heatmap|triggers|recent|check|candidates|log]")


if __name__ == "__main__":
    main()
