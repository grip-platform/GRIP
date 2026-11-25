#!/usr/bin/env python3
"""STE lint hook: score each assistant turn against Simplified Technical English.

Bound on two events (config/hooks.registry.json):

  Stop              Scores the turn, appends one row of numbers to
                    logs/ste-lint-hook.jsonl, and shows the operator one line.
  UserPromptSubmit  Hands that line to the model once, with the next prompt.
                    This half is off until GRIP_STE_FEEDBACK=1.

It only measures and displays. It refuses nothing, and it exits 0 on every path.
It prints nothing to stderr, also when a module it loads has a complaint.
GRIP_STE_VISIBLE=0 silences the line, and the row is still written.

This file and the modules it loads open no connection and start no process.
That is a statement about this process. The launcher that every GRIP hook
shares (lib/grip-python.sh) runs first, and when the venv is absent it makes
one and installs a package with pip. On an installed machine the venv exists
and the launcher only starts the interpreter.

Logic:  lib/ste_hook.py (it says why there are two events)
Scorer: lib/ste_lint.py
Tests:  tests/test_ste_lint_hook.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from pathlib import Path

# The scorer loads its dictionary with a plain "import ste_lexicon", so lib/
# itself must be on the path. The code comes from the tree that holds this file.
_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


def _stop(payload: dict):
    import ste_hook
    from claude_dir import get_claude_dir
    from ste_lint import score
    return ste_hook.on_stop(payload, os.environ, get_claude_dir(), Path.home(), score)


def _prompt(payload: dict):
    import ste_hook
    from claude_dir import get_claude_dir
    return ste_hook.on_prompt(payload, os.environ, get_claude_dir())


EVENTS = {"Stop": _stop, "UserPromptSubmit": _prompt}


def main() -> int:
    printed = None
    with contextlib.redirect_stderr(io.StringIO()):     # a loaded module may print a complaint
        try:
            payload = json.load(sys.stdin)
            handler = EVENTS.get(payload.get("hook_event_name")) if isinstance(payload, dict) else None
            printed = handler(payload) if handler else None
        except Exception:  # noqa: BLE001 -- a fault here must never fail a turn
            printed = None
    if printed:
        print(printed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
