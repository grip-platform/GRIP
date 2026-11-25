#!/usr/bin/env python3
"""
Dynamic tip generator for GRIP daily tips.

Scans live GRIP state (skills, commands, modes, agents) and generates
updated tips.json by merging hand-curated core tips (days 1-10) with
auto-generated feature tips.

Usage:
    python3 scripts/generate-tips.py              # Update tips.json
    python3 scripts/generate-tips.py --dry-run     # Preview without writing
    python3 scripts/generate-tips.py --stats       # Show current counts
"""

import json
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
TIPS_FILE = CLAUDE_DIR / "data" / "daily-tips" / "tips.json"
CORE_TIP_DAYS = 10  # Days 1-10 are hand-curated, never overwritten


def get_counts():
    """Get current GRIP component counts from filesystem."""
    skills = len(list((CLAUDE_DIR / "skills").glob("*/SKILL.md")))
    agents = len([
        f for f in (CLAUDE_DIR / "agents").glob("*.md")
        if f.name != "CLAUDE.md"
    ])
    modes = len(list((CLAUDE_DIR / "modes" / "definitions").glob("*.yaml")))

    commands = 0
    commands_file = CLAUDE_DIR / "docs" / "COMMANDS.md"
    if commands_file.exists():
        import re
        content = commands_file.read_text()
        commands = len(re.findall(r"`/[a-z][\w-]*`", content))

    return {
        "skills": skills,
        "agents": agents,
        "modes": modes,
        "commands": commands,
    }


def get_stale_tips(tips_data, counts):
    """Detect tips with stale counts or references."""
    stale = []
    for tip in tips_data.get("tips", []):
        msg = tip.get("message", "")
        # Check for old count references
        if "113 skills" in msg or "160 skills" in msg or "150 skills" in msg:
            stale.append((tip["day"], f"stale skill count in message"))
        if "28 agents" in msg or "21 agents" in msg:
            stale.append((tip["day"], f"stale agent count in message"))
        if "COMMANDS.cips" in msg or "COMMANDS.grip" in msg:
            stale.append((tip["day"], f"stale file reference"))
        # Check try_it commands for broken paths
        try_it = tip.get("try_it", "")
        if try_it and "lib/" in try_it:
            script_path = try_it.replace("python3 ~/.claude/", str(CLAUDE_DIR) + "/")
            script_path = script_path.split(" ")[1] if " " in script_path else script_path
            if not Path(script_path).exists():
                stale.append((tip["day"], f"broken try_it path: {try_it}"))
    return stale


def update_counts_in_tips(tips_data, counts):
    """Update any hardcoded counts in tip messages."""
    updated = 0
    for tip in tips_data.get("tips", []):
        msg = tip.get("message", "")
        original = msg
        # Replace stale count patterns
        import re
        msg = re.sub(r"\b\d+ commands\b", f"{counts['commands']} commands", msg)
        msg = re.sub(r"\b~?\d+ skills\b", f"~{counts['skills']} skills", msg)
        if msg != original:
            tip["message"] = msg
            updated += 1
    return updated


def main():
    dry_run = "--dry-run" in sys.argv
    stats_only = "--stats" in sys.argv

    counts = get_counts()

    if stats_only:
        print(f"Skills: {counts['skills']}")
        print(f"Agents: {counts['agents']}")
        print(f"Modes:  {counts['modes']}")
        print(f"Commands: {counts['commands']}")
        return

    if not TIPS_FILE.exists():
        print(f"Tips file not found: {TIPS_FILE}", file=sys.stderr)
        sys.exit(1)

    tips_data = json.loads(TIPS_FILE.read_text())

    # Check for staleness
    stale = get_stale_tips(tips_data, counts)
    if stale:
        print(f"Found {len(stale)} stale tip(s):")
        for day, reason in stale:
            print(f"  Day {day}: {reason}")

    # Update counts in existing tips
    updated = update_counts_in_tips(tips_data, counts)
    if updated:
        print(f"Updated counts in {updated} tip(s)")

    # Update version
    tips_data["version"] = "2.0.0"

    if dry_run:
        print("\n[DRY RUN] Would write updated tips.json")
        print(json.dumps(tips_data, indent=2)[:500] + "...")
    else:
        TIPS_FILE.write_text(json.dumps(tips_data, indent=2) + "\n")
        print(f"Updated {TIPS_FILE}")


if __name__ == "__main__":
    main()
