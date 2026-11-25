#!/usr/bin/env python3
"""Record when this session last went quiet, and arm the never-ending cycle.

ADR-0002 wave W1, the idle signal; ADR-0016 Step 7, its wiring.

``lib/infinite_cycle.decide`` needs to know how long a session has been idle
before it will let the never-ending cycle clear it. Nothing on disk answered
that. Four Stop hooks write timestamps, and none of them is usable:

  * ``ultraclear-checkpoint-stop.py`` is THROTTLED by design — at most one
    checkpoint per baseline window — so its timestamp can be a whole window
    behind the last Stop. A session that is busy every turn would read as idle,
    which is the one mistake this signal must not make;
  * ``otty-state-hook.sh idle`` delegates to Otty's own code-signed hook rather
    than writing anything GRIP can read, and is inert without Otty installed;
  * ``sentinel-hook.py`` and ``stop-failure-handler.py`` timestamp their own
    concerns, not session quiet.

So this writes one unthrottled fact per Stop: the wall-clock time the session
last finished a turn, with what the watcher needs to act on it (the full
session id, the transcript path, the working directory and the Claude Code
process id). The session id comes from the hook's own stdin payload, which is
authoritative for a hook; the environment is only a fallback.

WIRED PER LAUNCH ONLY. No shared settings file names this hook (a test fails
if one ever does). ``bin/grip`` adds it to ONE session's own ``--settings`` when
that launch sets ``GRIP_INFINITE_CYCLE=1`` (``lib/infinite_cycle_launch.py``):

    stop-idle-marker.py --arm-cycle --claude-pid "$PPID"

With ``--arm-cycle`` it also starts that Claude Code process's watcher
(``lib/infinite_cycle_runner.py``), unless one is already running or the brake
is engaged. It arms only AFTER the marker is written, so a new watcher can
never read the previous turn's Stop time and mistake a session that has just
answered for one that has been idle. ``$PPID`` is Claude Code's pid, exactly as
``hooks/otty-state-hook.sh`` uses it, and it is trusted only after
``sessions/<pid>.json`` confirms it names this session.

FAIL-OPEN BY CONSTRUCTION. Any error exits 0. A hook that can break a session
is worse than a missing idle signal — and a missing one is already handled:
the watcher waits when it cannot read a Stop time rather than assuming idle.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

try:  # a missing or broken lib must never break a session
    from infinite_cycle_state import (  # noqa: F401 - re-exported for callers and tests
        arm_watcher, claude_pid_arg, marker_path, process_session, read_last_stop,
        session_id, write_marker,
    )
except Exception:  # pragma: no cover - the fail-open path is tested by stubbing
    arm_watcher = claude_pid_arg = marker_path = process_session = None
    read_last_stop = session_id = write_marker = None


def _payload() -> dict:
    """The hook's stdin payload, or ``{}`` when there is none or it is not JSON."""
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _arm(claude_pid: int, sid: str) -> str:
    """Start the watcher for this Claude Code process; say what happened."""
    try:
        proc = process_session(claude_pid)
        if proc is None or (sid and proc.get("sessionId") != sid):
            return "not-armed: $PPID is not this session's Claude Code process"
        from grip_infinite import brake_state
        if brake_state().engaged:
            return "not-armed: the brake is engaged"
        return arm_watcher(claude_pid)
    except Exception as exc:  # noqa: BLE001 - fail-open
        return f"not-armed: {type(exc).__name__}"


def main(argv: list | None = None) -> int:
    try:
        payload = _payload()
        args = list(sys.argv[1:] if argv is None else argv)
        arm, claude_pid = "--arm-cycle" in args, claude_pid_arg(args)
        sid = str(payload.get("session_id") or "").strip()
        fields = {"session_id": sid or None, "transcript_path": payload.get("transcript_path"),
                  "cwd": payload.get("cwd"), "claude_pid": claude_pid or None}
        path, stamp = marker_path(sid=sid or None), time.time()
        write_marker(path, now=stamp, fields=fields)
        if arm:
            write_marker(path, now=stamp, fields={**fields, "cycle": _arm(claude_pid, sid)})
    except Exception:
        pass          # fail-open: never break a session over an idle signal
    return 0


if __name__ == "__main__":
    sys.exit(main())
