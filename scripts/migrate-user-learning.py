#!/usr/bin/env python3
"""
migrate-user-learning.py -- Split existing JSONL by operator field.

Copies (not moves) events to per-user directories.
Preserves the shared aggregated view.

Usage:
    python3 scripts/migrate-user-learning.py
    python3 scripts/migrate-user-learning.py --dry-run
"""

import json
import sys
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parent.parent
LEARNING_DIR = CLAUDE_DIR / "learning"

# Add lib to path
sys.path.insert(0, str(CLAUDE_DIR / "lib"))
sys.path.insert(0, str(CLAUDE_DIR))


def migrate(dry_run: bool = False) -> dict:
    """Split shared JSONL files into per-user directories."""
    from lib.user_detection import get_username

    default_user = get_username()
    files_to_migrate = [
        "converge-metrics.jsonl",
        "insights.jsonl",
    ]

    stats = {"files_processed": 0, "events_copied": 0, "users_found": set()}

    for filename in files_to_migrate:
        shared_path = LEARNING_DIR / filename
        if not shared_path.exists():
            continue

        user_events: dict[str, list[str]] = {}

        with open(shared_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Determine user from event or default
                user = event.get("operator", event.get("user", default_user))
                user = user.lower().split()[0] if user else default_user

                user_events.setdefault(user, []).append(line)

        for user, lines in user_events.items():
            user_dir = LEARNING_DIR / "users" / user
            if not dry_run:
                user_dir.mkdir(parents=True, exist_ok=True)
                user_file = user_dir / filename
                with open(user_file, "a") as uf:
                    for line in lines:
                        uf.write(line + "\n")

            stats["events_copied"] += len(lines)
            stats["users_found"].add(user)

        stats["files_processed"] += 1

    stats["users_found"] = sorted(stats["users_found"])
    return stats


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    prefix = "[DRY RUN] " if dry_run else ""

    print(f"{prefix}Migrating shared learning data to per-user directories...")
    stats = migrate(dry_run=dry_run)

    print(f"{prefix}Processed {stats['files_processed']} files")
    print(f"{prefix}Copied {stats['events_copied']} events")
    print(f"{prefix}Users found: {', '.join(stats['users_found'])}")
