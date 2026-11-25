#!/usr/bin/env python3
"""thinking-trace-preserve-hook.py — Stop event.

Preserve this session's reasoning-trace specimens (the ``{"type":"thinking"}``
blocks already in the transcript) into the durable, compaction-proof store, so
the reasoning survives past the session and past a /compact — as a
NON-AUTHORITATIVE diagnostic specimen, never as evidence of cause
(`lib/thinking_trace.py` carries the fence; the rationale is
`drafts/cc-paper/thinking-traces-essay.md`).

Opt-in, default OFF (mirrors wa-thinking-stream-hook): reasoning capture should
be a choice, not an imposed per-turn cost. Enable per-session:

    export GRIP_THINKING_TRACE_PRESERVE=on
    grip

Advisory + fail-open: never blocks session close, never raises, always exits 0.
Idempotent by content address — running on every Stop only writes genuinely-new
specimens.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ENV_TOGGLE = "GRIP_THINKING_TRACE_PRESERVE"


def _parse_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:  # noqa: BLE001 — fail-open on malformed stdin
        return {}


def main() -> int:
    # Fast-path: opt-in only.
    if os.environ.get(_ENV_TOGGLE, "").strip().lower() not in ("on", "1", "true", "yes"):
        return 0

    payload = _parse_stdin()
    transcript_path = payload.get("transcript_path") or None
    session_id = payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or None
    if not transcript_path and not session_id:
        return 0

    try:
        # Resolve the library from the checkout that CONTAINS this hook first,
        # so a worktree or a test exercises THIS tree's code rather than
        # whatever $HOME/.claude happens to hold (tests-must-be-able-to-fail
        # mandate 2: explicit → checkout → home). Fall back to $CLAUDE_DIR/lib,
        # then ~/.claude/lib, for an installed copy that lives elsewhere.
        here_lib = Path(__file__).resolve().parent.parent / "lib"
        env_lib = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude")) / "lib"
        lib = here_lib if (here_lib / "thinking_trace.py").is_file() else env_lib
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        import thinking_trace  # noqa: E402

        res = thinking_trace.preserve_session(
            session_id=session_id, transcript_path=transcript_path
        )
        if res.get("ok") and res.get("new"):
            print(
                f"[thinking-trace] preserved {res['new']} new specimen(s) "
                f"({res['total']} total) → state/thinking-traces/{res.get('session_id','')}",
                file=sys.stderr,
            )
    except Exception:  # noqa: BLE001 — advisory; never break session close
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
