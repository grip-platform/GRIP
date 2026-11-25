#!/usr/bin/env python3
"""python -m lib.grip_anywhere — the module entry point.

Stealth-by-default CLI: a no-flag run is analysis-only and never touches
the upstream repository; writes require --non-stealth --owner-consent.
--report emits the markdown RSI report (analysis only). The runner's
exit code is propagated: 0 on completion, 1 on run failure; with
--fail-on-unconverged, 1 also when the quality gate was not met.

Both entry paths work — `python -m lib.grip_anywhere` and
`python3 lib/grip_anywhere/__main__.py` — routing to the same
orchestrator as the script path (lib/grip_anywhere/runner.py) and the
import path (from lib.grip_anywhere import GripAnywhere): one engine,
three doors.
"""
import sys
from pathlib import Path

# Ensure the repo root is importable when run directly as a script
# (e.g. `python3 lib/grip_anywhere/__main__.py` from anywhere). Guard the
# path being INSERTED (the repo root), not the lib dir itself — guarding
# _LIB while inserting _LIB.parent skips the bootstrap exactly when a bare
# lib/ directory is already on sys.path.
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.grip_anywhere.runner import main

if __name__ == "__main__":
    sys.exit(main())
