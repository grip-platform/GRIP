#!/usr/bin/env python3
"""PreToolUse advisory: a bare `python3` about to run GRIP code.

Reads the Bash payload, and when the command invokes GRIP code under the system
interpreter it prints ONE stderr line naming the `grip-py` equivalent. It never
blocks, never rewrites the command, and always exits 0.

Detector: lib/system_python_detector.py (pure, zero side effects)
Tests:    tests/test_system_python_detector.py
Audit:    logs/system-python-hook.jsonl (one line per flagged command)
Rule:     rules/never-system-python-for-grip.md
Shim:     bin/grip-py

Origin: 2026-09-14. `python3 ~/.claude/lib/grip_save.py` was typed by hand and
did not fail -- it downgraded the IDR seal to a forgeable demo seal, announced
it in one warning above a wall of green ticks, and reported `12 passed,
0 failed`. The tool was then reported to the operator as broken, when the
invocation was.

WARN ONLY, WITH NO PROMOTION PATH TO DENY. This is a deliberate design decision,
not a staging step. A gate able to refuse a `python3` invocation could refuse the
command that repairs a broken venv -- `rules/gate-resolution-escape-hatch.md`
Invariant 1, and a deadlock GRIP has already hit once, on 2026-06-08, where only
an external human could break it. Because it denies nothing, it passes that
rule's escape-hatch audit trivially.

The interpreter that runs THIS hook is irrelevant to its verdict: the detector is
pure-stdlib and imports nothing from the venv, so the advisory fires whether or
not `cryptography` is present. A hook that needed the venv to warn you about not
using the venv would be silent exactly when it was needed.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_CLAUDE = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))
if str(_CLAUDE) not in sys.path:
    sys.path.insert(0, str(_CLAUDE))

try:
    from lib.system_python_detector import advisory, scan
except Exception:  # noqa: BLE001 -- a broken import must never block a Bash call
    sys.exit(0)

_AUDIT = _CLAUDE / "logs" / "system-python-hook.jsonl"


def _audit(row: dict) -> None:
    """Best-effort. A failed audit write must not cost the operator a command."""
    try:
        _AUDIT.parent.mkdir(parents=True, exist_ok=True)
        row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with _AUDIT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    try:
        if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
            return 0
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str):
            return 0

        findings = scan(command)
        if not findings:
            return 0

        print(advisory(findings), file=sys.stderr)
        _audit({
            "action": "advised",
            "session_id": payload.get("session_id"),
            "count": len(findings),
            # The matched SUBSTRING, not the whole command: enough to adjudicate
            # the verdict later without copying arbitrary shell into a log file.
            "matched": [f.matched for f in findings][:5],
        })
    except Exception as exc:  # noqa: BLE001 -- fail-open is the contract
        _audit({"action": "error", "error": repr(exc)[:300]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
