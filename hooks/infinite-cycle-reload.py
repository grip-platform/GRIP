#!/usr/bin/env python3
"""The never-ending cycle's reload: a Claude Code SessionStart hook, matcher ``clear``.

ADR-0016 Step 7, W1. When the cycle's watcher (``lib/infinite_cycle_runner.py``)
sends ``/clear``, it first leaves a handoff for this Claude Code process. On the
fresh start that follows, this hook:

* binds the new session id to the thread the old one wrote to (Step 4's signed
  thread store), so the chain continues instead of forking;
* injects the continuity block: open tasks, the last "## Next Step", the
  decision turn ids, the newest tool results, and the thread;
* injects the HOT turns itself ONLY when the shared warm reload
  (``hooks/ultraclear-reload-sessionstart.py``) will not serve this checkpoint,
  so the turns are never lost and never doubled by this hook;
* marks the handoff consumed, with what it did, for the watcher to record.

A start that is not one of the cycle's clears (a manual ``/clear``, a stale
handoff, another process's handoff) gets ``{}`` and nothing changes.

WIRED PER LAUNCH ONLY, by ``bin/grip`` when ``GRIP_INFINITE_CYCLE=1``:

    infinite-cycle-reload.py --claude-pid "$PPID"

CRASH-SAFE: any error prints ``{}``. A SessionStart hook can never break a
session; the worst case is a clear whose continuity block is missing, which the
watcher then records as a failed cycle.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


def main(argv: list | None = None) -> int:
    try:
        from infinite_cycle_continuity import reload_after_clear
        from infinite_cycle_state import claude_pid_arg
        payload = json.loads(sys.stdin.read() or "{}")
        args = list(sys.argv[1:] if argv is None else argv)
        out = reload_after_clear(payload if isinstance(payload, dict) else {},
                                 claude_pid=claude_pid_arg(args), now=time.time())
    except Exception:
        out = {}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
