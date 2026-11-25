#!/usr/bin/env python3
"""Implementation backend for ``bin/precog-window``.

Separating this from the bash wrapper closes a shell-injection hole that
existed in the V1 heredoc form: ``--reason "'; import os; os.system(...);"``
would have executed arbitrary Python because the wrapper interpolated
``$HOURS`` and ``$REASON`` into a ``python -c`` heredoc unquoted.

Argv passes through bash unchanged (no string interpolation), so ``argparse``
receives the raw values and the user cannot inject Python.

Subcommands:
    open --hours N [--reason TEXT]   open the W4 flag window for N hours
    close                            flip the flag off immediately
    status                           print current resolver state (default)

Exit codes:
    0   success
    2   argparse usage error / missing argument
"""
from __future__ import annotations

import argparse
import json
import sys

# Local-import bootstrap (same pattern as scripts/precog_smoke.py)
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lib.precog.state import (  # noqa: E402
    open_flag_window,
    close_flag_window,
    flag_window_status,
    is_precog_enabled,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="precog-window")
    sub = parser.add_subparsers(dest="cmd")

    p_open = sub.add_parser("open", help="open the W4 flag window for N hours")
    p_open.add_argument("--hours", type=float, required=True,
                        help="window duration in hours (must be > 0)")
    p_open.add_argument("--reason", default="",
                        help="free-text reason recorded with the window")

    sub.add_parser("close", help="flip the flag off immediately")
    sub.add_parser("status", help="print current resolver state (default)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "open":
        if args.hours <= 0:
            print(f"error: --hours must be > 0, got {args.hours}", file=sys.stderr)
            return 2
        record = open_flag_window(hours=args.hours, reason=args.reason)
        print(json.dumps(record, indent=2, default=str))
        return 0
    if args.cmd == "close":
        record = close_flag_window()
        print(json.dumps(record, indent=2, default=str))
        return 0
    # default → status
    record = flag_window_status()
    record["is_precog_enabled"] = is_precog_enabled()
    print(json.dumps(record, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
