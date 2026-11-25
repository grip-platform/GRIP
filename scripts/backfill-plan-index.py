#!/usr/bin/env python3
"""
Backfill Plan Index - Retroactively link existing plans to sessions

Scans session JSONL files for plan references and links them in the registry.
Part of Cortex Integration Phase 3.

Usage:
    python3 backfill-plan-index.py           # Run backfill
    python3 backfill-plan-index.py --dry-run # Preview without changes
    python3 backfill-plan-index.py --embed   # Also embed all plans
"""

import sys
import json
import argparse
from pathlib import Path

# Add lib to path for imports
CLAUDE_DIR = Path.home() / ".claude"
sys.path.insert(0, str(CLAUDE_DIR / "lib"))

# Import with hyphen in filename (plan-registry.py)
import importlib.util
spec = importlib.util.spec_from_file_location("plan_registry", CLAUDE_DIR / "lib" / "plan-registry.py")
plan_registry_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan_registry_module)
PlanRegistry = plan_registry_module.PlanRegistry


def main():
    parser = argparse.ArgumentParser(
        description="Backfill plan index from session history"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying index"
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help="Also embed all plans after backfill"
    )
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=CLAUDE_DIR / "projects",
        help="Directory containing session files"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("GRIP Plan Index Backfill")
    print("=" * 60)
    print()

    # Check plans directory
    plans_dir = CLAUDE_DIR / "plans"
    plan_files = list(plans_dir.glob("*.md"))
    print(f"Found {len(plan_files)} plan files in {plans_dir}")
    print()

    if args.dry_run:
        print("[DRY RUN] No changes will be made")
        print()

        # Just scan and report
        registry = PlanRegistry()
        linked = 0
        orphaned = 0

        for plan_path in plan_files:
            plan_id = plan_path.stem
            session_link = registry._find_session_reference(
                plan_id,
                args.session_dir
            )

            if session_link:
                linked += 1
                if args.verbose:
                    print(f"  [LINKED] {plan_id}")
                    print(f"           -> Instance: {session_link.get('instance_id')}")
                    print(f"           -> Project: {session_link.get('project')}")
            else:
                orphaned += 1
                if args.verbose:
                    print(f"  [ORPHAN] {plan_id}")

        print()
        print("Summary (dry run):")
        print(f"  Total plans: {len(plan_files)}")
        print(f"  Would link: {linked}")
        print(f"  Orphaned: {orphaned}")
        if plan_files:
            print(f"  Link rate: {linked / len(plan_files) * 100:.1f}%")
        return

    # Run actual backfill
    print("Running backfill...")
    registry = PlanRegistry()

    try:
        stats = registry.backfill(args.session_dir)

        print()
        print("Backfill complete:")
        print(f"  Total plans: {stats['total']}")
        print(f"  Linked: {stats['linked']}")
        print(f"  Orphaned: {stats['orphaned']}")
        if stats['total'] > 0:
            print(f"  Link rate: {stats['linked'] / stats['total'] * 100:.1f}%")

        if args.embed:
            print()
            print("Embedding plans...")
            try:
                embedded = registry.embed_all_plans()
                print(f"  Embedded: {embedded} plans")
            except ImportError as e:
                print(f"  [SKIP] Embeddings not available: {e}")
            except Exception as e:
                print(f"  [ERROR] Embedding failed: {e}")

        # Show final stats
        print()
        print("Index statistics:")
        final_stats = registry.get_stats()
        print(json.dumps(final_stats, indent=2))

    except Exception as e:
        print(f"[ERROR] Backfill failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
