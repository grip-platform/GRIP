#!/usr/bin/env python3
"""Run the lossless gate (lib/ste_lossless.py) from the command line.

    ste-lossless-check.py --source A.md --rewrite B.md        one rewrite: exit 1 if refused
    ste-lossless-check.py --pairs docs/measurements/2026-10-05-ste-readability-pairs.json
                                                              the H-S4 reading over the twenty pairs
    ... --json                                                the same as JSON

Plan: plans/ULTRAPLAN-2026-10-10-ste-improver-loop.md, stage S4.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "lib"))

import ste_lossless  # noqa: E402


def load_pairs(path: Path) -> list:
    """(id, source, rewrite) rows from the readability-pairs file, which names them
    original and rewrite."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["pairs"] if isinstance(data, dict) else data
    return [{"id": r["id"], "source": r["original"], "rewrite": r["rewrite"]} for r in rows]


def _one(args: argparse.Namespace) -> int:
    result = ste_lossless.check(Path(args.source).read_text(encoding="utf-8"),
                                Path(args.rewrite).read_text(encoding="utf-8"))
    if args.json:
        print(json.dumps({"ok": result.ok, "errors": result.errors, "warnings": result.warnings,
                          "dropped": result.dropped}, indent=2))
    else:
        print("ok" if result.ok else "REFUSED")
        for line in result.lines():
            print("  " + line)
    return 0 if result.ok else 1


def _pairs(args: argparse.Namespace) -> int:
    pairs = load_pairs(Path(args.pairs))
    out = ste_lossless.verdict_on_pairs(pairs, args.max_refused)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"{out['verdict']}: {len(out['refused'])} of {out['pairs']} faithful rewrites refused "
              f"(max {out['max_refused']}) {out['refused']}; planted drops missed: "
              f"{len(out['planted_missed'])} of {out['planted']} {out['planted_missed']}")
        for p in pairs:
            r = ste_lossless.check(p["source"], p["rewrite"])
            if not r.ok:
                print(f"  {p['id']}: " + "; ".join(r.errors))
    return 0 if out["verdict"] == "CONFIRMED" else 1


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source")
    ap.add_argument("--rewrite")
    ap.add_argument("--pairs")
    ap.add_argument("--max-refused", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.pairs:
        return _pairs(args)
    if args.source and args.rewrite:
        return _one(args)
    ap.error("give --pairs FILE, or --source FILE and --rewrite FILE")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
