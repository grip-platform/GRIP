#!/usr/bin/env python3
"""Emit the frozen fingerprint of a signed-ledger era, as JSON and as prose.

The genesis record of the post-compromise era commits to the digest this prints.
That only means anything if a third party can recompute it, so this script reads
the ledger and nothing else — no GRIP state, no key, no network.

    python3 scripts/idr-freeze-report.py                    # both live ledgers
    python3 scripts/idr-freeze-report.py --ledger path.jsonl
    python3 scripts/idr-freeze-report.py --json             # machine-readable

Exit codes are three-valued, deliberately:

    0  every record in every ledger was placed — the freeze is COMPLETE
    1  a ledger could not be read at all
    3  the freeze is INCOMPLETE: records exist that could not be placed

3 is not folded into 0. "I fingerprinted 99% of this and cannot see the rest" is
a different answer from "I fingerprinted this", and a tool that blurs them is
how 852 of 866 records went missing without anyone noticing.
"""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.claude_dir import get_claude_dir  # noqa: E402
from lib.idr_freeze import freeze_commitment, freeze_ledger  # noqa: E402

EXIT_OK, EXIT_UNREADABLE, EXIT_INCOMPLETE = 0, 1, 3


def default_ledgers() -> list[Path]:
    base = get_claude_dir() / "state" / "precog"
    return [base / "idr.jsonl", base / "context.jsonl"]


def render(freeze, commitment: str) -> str:
    lines = [
        f"  ledger            {freeze.path}",
        f"  records in file   {freeze.records}",
        f"  size              {freeze.size_bytes} bytes",
        f"  sha256 (file)     {freeze.sha256_file}",
        f"  sha256 (records)  {freeze.sha256_records}",
        f"  merkle root       {freeze.merkle_root}",
        f"  placed in forest  {freeze.forest_nodes}",
        f"  anchored          {freeze.anchored}",
        f"  unanchored        {freeze.unanchored}",
        f"  chain starts      {freeze.true_roots} true roots "
        f"+ {freeze.fragment_roots} fragment roots (severed above)",
        f"  linked children   {freeze.records - freeze.true_roots - freeze.fragment_roots}",
        f"  exogenous anchors {list(freeze.anchors) or 'NONE'}",
        f"  COMMITMENT        {commitment}",
    ]
    if freeze.duplicates:
        lines.append(f"  DUPLICATE ids     {len(freeze.duplicates)}: "
                     f"{', '.join(freeze.duplicates[:5])}")
    if freeze.orphans:
        lines.append(f"  INCOMPLETE — {len(freeze.orphans)} record(s) could not be placed:")
        for rid, pred in freeze.orphans[:10]:
            lines.append(f"      {rid}  wants predecessor  {pred}")
        if len(freeze.orphans) > 10:
            lines.append(f"      ... and {len(freeze.orphans) - 10} more")
    else:
        lines.append("  COMPLETE — every record in the file was placed")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", action="append", type=Path,
                    help="ledger to freeze; repeatable. Default: both live ledgers.")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="emit machine-readable JSON instead of prose")
    args = ap.parse_args(argv)

    ledgers = args.ledger or default_ledgers()
    results, incomplete = [], False

    for path in ledgers:
        if not path.exists():
            print(f"error: ledger not found: {path}", file=sys.stderr)
            return EXIT_UNREADABLE
        freeze = freeze_ledger(path)
        commitment = freeze_commitment(freeze)
        incomplete = incomplete or bool(freeze.orphans)
        results.append({**asdict(freeze), "commitment": commitment})
        if not args.as_json:
            print(f"=== {path.name} ===")
            print(render(freeze, commitment))
            print()

    if args.as_json:
        print(json.dumps(results, indent=2, sort_keys=True))

    return EXIT_INCOMPLETE if incomplete else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
