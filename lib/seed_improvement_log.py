#!/usr/bin/env python3
"""
Seed improvement-log.jsonl from git history and metrics.jsonl from system state.

Phase 1: Parse git commits into improvement entries, deduplicate, append.
Phase 2: Generate metrics.jsonl from skill directories, pattern counts, skill usage.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
IMPROVEMENT_LOG = CLAUDE_DIR / "improvement-log.jsonl"
METRICS_FILE = CLAUDE_DIR / "metrics.jsonl"
PATTERN_COUNTS = CLAUDE_DIR / "pattern-counts.json"
SKILL_USAGE = CLAUDE_DIR / "skill-usage.jsonl"
SKILLS_DIR = CLAUDE_DIR / "skills"

# Commit prefix to improvement type mapping
PREFIX_MAP = {
    "feat": "efficiency_improvement",
    "fix": "bug_fix",
    "refactor": "refactor",
    "docs": "documentation_update",
    "security": "gate_added",
    "test": "efficiency_improvement",
    "chore": "refactor",
    "perf": "efficiency_improvement",
    "style": "refactor",
}

# Commit prefix to trigger mapping
TRIGGER_MAP = {
    "feat": "manual",
    "fix": "friction_encountered",
    "refactor": "friction_encountered",
    "docs": "manual",
    "security": "friction_encountered",
    "test": "manual",
    "chore": "proactive",
    "perf": "proactive",
    "style": "proactive",
}

# Special feat commits that created skills
SKILL_KEYWORDS = ["add", "skill", "mode", "agent", "gate", "broly", "kono"]


def run_git(args: list[str]) -> str:
    """Run a git command in the CLAUDE_DIR repo."""
    result = subprocess.run(
        ["git", "-C", str(CLAUDE_DIR)] + args,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def load_existing_descriptions() -> set:
    """Load existing improvement descriptions to deduplicate."""
    descriptions = set()
    if IMPROVEMENT_LOG.exists():
        for line in IMPROVEMENT_LOG.read_text().strip().split("\n"):
            if line:
                try:
                    entry = json.loads(line)
                    descriptions.add(entry.get("description", ""))
                except json.JSONDecodeError:
                    continue
    return descriptions


def extract_component(commit_hash: str) -> str:
    """Extract component from changed files in a commit."""
    files_output = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", commit_hash])
    if not files_output:
        return "unknown"

    files = files_output.strip().split("\n")
    # Group by top-level directory
    dirs = set()
    for f in files:
        parts = f.split("/")
        if len(parts) > 1:
            dirs.add(parts[0])
        else:
            dirs.add(f)

    if not dirs:
        return "unknown"

    # Return up to 3 most relevant directories
    priority = ["lib", "hooks", "skills", "rules", "bin", "scripts", "docs", "mcp-servers"]
    sorted_dirs = sorted(dirs, key=lambda d: priority.index(d) if d in priority else 99)
    return ",".join(sorted_dirs[:3])


def parse_commit_prefix(message: str) -> tuple:
    """Parse commit message into (prefix, description)."""
    if ":" not in message:
        return None, message

    prefix_part = message.split(":", 1)[0].strip().lower()
    description = message.split(":", 1)[1].strip()

    # Handle scoped prefixes like feat(scope)
    base_prefix = prefix_part.split("(")[0]

    if base_prefix in PREFIX_MAP:
        return base_prefix, description
    return None, message


def determine_type(prefix: str, description: str) -> str:
    """Determine improvement type, with skill_created override for certain feat commits."""
    if prefix == "feat":
        desc_lower = description.lower()
        for keyword in SKILL_KEYWORDS:
            if keyword in desc_lower:
                return "skill_created"
    return PREFIX_MAP.get(prefix, "efficiency_improvement")


def seed_improvements():
    """Phase 1: Seed improvement-log.jsonl from git history."""
    print("Phase 1: Seeding improvement-log.jsonl from git history...")

    existing = load_existing_descriptions()
    print(f"  Existing entries: {len(existing)}")

    # Get last 50 commits
    log_output = run_git(["log", "--oneline", "--format=%H|%s|%aI", "-50"])
    if not log_output:
        print("  No git history found.")
        return 0

    commits = log_output.strip().split("\n")
    new_entries = []

    for line in commits:
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue

        commit_hash, message, timestamp = parts
        prefix, description = parse_commit_prefix(message)

        if prefix is None:
            continue

        # Skip if description already exists (dedup)
        if description in existing:
            continue

        component = extract_component(commit_hash)
        imp_type = determine_type(prefix, description)
        trigger = TRIGGER_MAP.get(prefix, "manual")

        entry = {
            "timestamp": timestamp,
            "type": imp_type,
            "component": component,
            "description": description,
            "trigger": trigger,
            "source": "git_seed",
            "commit": commit_hash[:8],
        }

        new_entries.append(entry)
        existing.add(description)

    # Append to log
    if new_entries:
        with open(IMPROVEMENT_LOG, "a") as f:
            for entry in new_entries:
                f.write(json.dumps(entry) + "\n")

    print(f"  Added {len(new_entries)} entries from git history.")
    return len(new_entries)


def seed_metrics():
    """Phase 2: Seed metrics.jsonl from system state."""
    print("\nPhase 2: Seeding metrics.jsonl from system state...")

    entries = []

    # 1. Skill directories -> skill_generated events
    if SKILLS_DIR.exists():
        skill_dirs = sorted([d for d in SKILLS_DIR.iterdir() if d.is_dir()])
        print(f"  Found {len(skill_dirs)} skill directories.")

        for skill_dir in skill_dirs:
            skill_name = skill_dir.name
            # Try to get creation date from git log
            git_date = run_git([
                "log", "--diff-filter=A", "--format=%aI",
                "--reverse", "-1", "--", f"skills/{skill_name}"
            ])
            if not git_date:
                # Fallback: use directory mtime
                mtime = skill_dir.stat().st_mtime
                git_date = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

            entries.append({
                "timestamp": git_date.strip(),
                "event": "skill_generated",
                "skill": skill_name,
                "source": "seed",
            })

    # 2. Pattern counts -> patterns_detected events
    if PATTERN_COUNTS.exists():
        try:
            data = json.loads(PATTERN_COUNTS.read_text())
            for pattern_name, info in data.items():
                if pattern_name == "_meta":
                    continue
                count = info.get("count", 0)
                last_seen = info.get("last_seen", datetime.now(timezone.utc).isoformat())
                entries.append({
                    "timestamp": last_seen,
                    "event": "patterns_detected",
                    "pattern": pattern_name,
                    "count": count,
                    "skill_created": info.get("skill_created", False),
                    "source": "seed",
                })
            print(f"  Found {len(data) - 1} patterns (excluding _meta).")
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  Error reading pattern-counts.json: {e}")

    # 3. Skill usage -> skills_triggered events
    if SKILL_USAGE.exists():
        usage_lines = SKILL_USAGE.read_text().strip().split("\n")
        for line in usage_lines:
            if not line:
                continue
            try:
                usage = json.loads(line)
                entries.append({
                    "timestamp": usage.get("date", datetime.now(timezone.utc).isoformat()),
                    "event": "skills_triggered",
                    "skill": usage.get("skill", "unknown"),
                    "trigger_source": usage.get("source", "unknown"),
                    "source": "seed",
                })
            except json.JSONDecodeError:
                continue
        print(f"  Found {len(usage_lines)} skill usage entries.")

    # Write metrics
    if entries:
        with open(METRICS_FILE, "a") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    print(f"  Wrote {len(entries)} metric entries.")
    return len(entries)


def main():
    print("=== GRIP Improvement Log Seeder ===\n")

    imp_count = seed_improvements()
    met_count = seed_metrics()

    print(f"\n=== Done ===")
    print(f"  Improvements: {imp_count} new entries")
    print(f"  Metrics: {met_count} new entries")

    # Show totals
    if IMPROVEMENT_LOG.exists():
        total_imp = len([l for l in IMPROVEMENT_LOG.read_text().strip().split("\n") if l])
        print(f"  Total improvement-log.jsonl entries: {total_imp}")
    if METRICS_FILE.exists():
        total_met = len([l for l in METRICS_FILE.read_text().strip().split("\n") if l])
        print(f"  Total metrics.jsonl entries: {total_met}")


if __name__ == "__main__":
    main()
