#!/usr/bin/env python3
"""
memory_metabolism.py — Periodic memory maintenance runner.

Runs KONO metabolism (fitness decay), consolidation (attestation -> permanence),
and pruning (dead memory cleanup). Designed for both session-end hook calls
and periodic launchd/cron execution.

Usage:
    python3 lib/memory_metabolism.py              # Run all maintenance
    python3 lib/memory_metabolism.py --dry-run     # Show what would happen
    python3 lib/memory_metabolism.py --json         # JSON output
    python3 lib/memory_metabolism.py --prune-threshold 0.05  # Custom threshold
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
LIB_DIR = CLAUDE_DIR / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


def run_metabolism(dry_run: bool = False) -> dict:
    """Run KONO fitness decay on stale memories."""
    try:
        from kono_substrate import MemorySubstrate
        substrate = MemorySubstrate()

        if dry_run:
            stats = substrate.get_stats() if hasattr(substrate, 'get_stats') else {}
            return {"ok": True, "dry_run": True, "stats": stats}

        affected = substrate.run_metabolism()
        return {"ok": True, "affected": affected}
    except ImportError:
        return {"ok": False, "error": "kono_substrate not available"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def run_consolidation(dry_run: bool = False) -> dict:
    """Promote attested memories to permanent status."""
    try:
        from kono_substrate import MemorySubstrate
        substrate = MemorySubstrate()

        if dry_run:
            return {"ok": True, "dry_run": True}

        count = substrate.consolidate()
        return {"ok": True, "consolidated": count}
    except ImportError:
        return {"ok": False, "error": "kono_substrate not available"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def run_pruning(threshold: float = 0.05, dry_run: bool = False) -> dict:
    """Remove memories below fitness threshold."""
    try:
        from kono_substrate import MemorySubstrate
        substrate = MemorySubstrate()

        result = substrate.prune(below_fitness=threshold, dry_run=dry_run)
        return {"ok": True, **result}
    except ImportError:
        return {"ok": False, "error": "kono_substrate not available"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def run_session_archiving(days: int = 7) -> dict:
    """Compress old session JSONL files."""
    try:
        from session_archiver import SessionArchiver
        archiver = SessionArchiver(max_age_days=days)
        result = archiver.archive_old_sessions()
        return {"ok": True, **result}
    except ImportError:
        return {"ok": False, "error": "session_archiver not available"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def run_all(
    dry_run: bool = False,
    prune_threshold: float = 0.05,
    archive_days: int = 7,
) -> dict:
    """Run complete memory maintenance cycle."""
    start = time.time()

    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dry_run": dry_run,
        "metabolism": run_metabolism(dry_run=dry_run),
        "consolidation": run_consolidation(dry_run=dry_run),
        "pruning": run_pruning(threshold=prune_threshold, dry_run=dry_run),
        "archiving": run_session_archiving(days=archive_days),
        "elapsed_s": 0.0,
    }

    results["elapsed_s"] = round(time.time() - start, 2)

    # Summary
    ok_count = sum(1 for k in ["metabolism", "consolidation", "pruning", "archiving"]
                   if results[k].get("ok"))
    results["summary"] = f"{ok_count}/4 subsystems healthy"
    # Metabolism + consolidation are required; pruning + archiving are optional
    results["ok"] = (
        results["metabolism"].get("ok", False)
        and results["consolidation"].get("ok", False)
    )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="GRIP Memory Metabolism Runner")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--prune-threshold", type=float, default=0.05,
                        help="Fitness threshold for pruning (default: 0.05)")
    parser.add_argument("--archive-days", type=int, default=7,
                        help="Archive sessions older than N days (default: 7)")

    args = parser.parse_args()

    results = run_all(
        dry_run=args.dry_run,
        prune_threshold=args.prune_threshold,
        archive_days=args.archive_days,
    )

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        prefix = "[DRY RUN] " if args.dry_run else ""
        print(f"{prefix}Memory Metabolism: {results['summary']} ({results['elapsed_s']}s)")
        for subsystem in ["metabolism", "consolidation", "pruning", "archiving"]:
            r = results[subsystem]
            status = "OK" if r.get("ok") else f"FAIL: {r.get('error', 'unknown')}"
            detail = ""
            if r.get("affected"):
                detail = f" ({r['affected']} affected)"
            elif r.get("consolidated"):
                detail = f" ({r['consolidated']} promoted)"
            print(f"  {subsystem}: {status}{detail}")


if __name__ == "__main__":
    main()
