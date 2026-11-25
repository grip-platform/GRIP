"""Purge test/garbage data from decision-log.jsonl.

Removes test entries (test-0 through test-9 patterns), their corresponding
outcome entries, and rebuilds the hash chain for integrity.

Usage:
    python3 lib/copilot/decision_purge.py preview    # Show what would be purged
    python3 lib/copilot/decision_purge.py execute     # Actually purge
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set

_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from lib.audit_chain import sign_entry
from lib.aide.atomic_io import read_jsonl, locked_read_modify_write

TEST_PATTERN = re.compile(r"^test[-_]?\d*$", re.IGNORECASE)

COPILOT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "aide"
DECISION_LOG = COPILOT_DIR / "decision-log.jsonl"


def identify_test_entries(entries: List[Dict]) -> tuple[Set[str], List[Dict], List[Dict]]:
    """Identify test decision IDs and their orphaned outcomes.

    Returns:
        (test_ids, test_decisions, orphaned_outcomes)
    """
    test_ids: Set[str] = set()
    test_decisions: List[Dict] = []

    for entry in entries:
        if entry.get("type") == "outcome":
            continue
        what = str(entry.get("decision", {}).get("what", ""))
        if TEST_PATTERN.match(what):
            test_ids.add(entry.get("id", ""))
            test_decisions.append(entry)

    orphaned_outcomes = [
        e for e in entries
        if e.get("type") == "outcome" and e.get("decision_id", "") in test_ids
    ]

    return test_ids, test_decisions, orphaned_outcomes


def purge(log_path: Path = DECISION_LOG, dry_run: bool = True) -> Dict:
    """Purge test data and rebuild hash chain.

    Args:
        log_path: Path to decision-log.jsonl
        dry_run: If True, only preview what would be purged

    Returns:
        Summary dict with purge counts and chain status
    """
    entries = read_jsonl(log_path)
    test_ids, test_decisions, orphaned_outcomes = identify_test_entries(entries)

    purge_ids = test_ids | {e.get("id", "") for e in orphaned_outcomes}
    kept = [
        e for e in entries
        if e.get("id", "") not in purge_ids
        and not (e.get("type") == "outcome" and e.get("decision_id", "") in test_ids)
    ]

    summary = {
        "total_before": len(entries),
        "test_decisions_purged": len(test_decisions),
        "orphaned_outcomes_purged": len(orphaned_outcomes),
        "total_purged": len(test_decisions) + len(orphaned_outcomes),
        "remaining": len(kept),
        "dry_run": dry_run,
    }

    if dry_run:
        return summary

    # Reset chain state to genesis before rebuilding
    from lib.audit_chain import _save_chain_state
    _save_chain_state({"last_chain_hash": "", "sequence": 0})

    # Rebuild hash chain: re-sign each entry in order
    resigned = []
    for entry in kept:
        # Strip old chain metadata before re-signing
        clean = {k: v for k, v in entry.items() if k not in {"entry_hash", "chain_hash", "sequence"}}
        signed = sign_entry(clean)
        resigned.append(signed)

    # Write atomically
    locked_read_modify_write(log_path, resigned)

    # Verify chain after rebuild
    from lib.audit_chain import verify_chain
    integrity = verify_chain(log_path)
    summary["chain_valid"] = integrity.get("valid", False)
    summary["chain_entries"] = integrity.get("total_entries", 0)

    return summary


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "preview"

    if cmd == "preview":
        result = purge(dry_run=True)
        print(f"Purge Preview")
        print(f"=============")
        print(f"Total entries:          {result['total_before']}")
        print(f"Test decisions:         {result['test_decisions_purged']}")
        print(f"Orphaned outcomes:      {result['orphaned_outcomes_purged']}")
        print(f"Total to purge:         {result['total_purged']}")
        print(f"Remaining after purge:  {result['remaining']}")
        print(f"\nRun with 'execute' to apply.")
    elif cmd == "execute":
        result = purge(dry_run=False)
        print(f"Purge Complete")
        print(f"==============")
        print(f"Purged:    {result['total_purged']} entries")
        print(f"Remaining: {result['remaining']} entries")
        print(f"Chain:     {'VERIFIED' if result.get('chain_valid') else 'BROKEN'}")
    else:
        print(f"Usage: {sys.argv[0]} [preview|execute]")
        sys.exit(1)


if __name__ == "__main__":
    main()
