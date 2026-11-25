#!/usr/bin/env python3
"""W10 PreToolUse hook — surface fresh sidecar entries on Edit/Write.

Per plans/auto-insight-rsi-sprint-2026-05-05.md W10.2. Fires before
Edit / Write tool calls. If the target has a co-located <target>.md.insights
sidecar, the hook reads it, filters to fresh entries (default 30 days),
and surfaces them as additionalContext in PreToolUse hookSpecificOutput.

Fails open: any exception silently returns no additionalContext, never
blocks the edit.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path


_LIB_OVERRIDE = os.environ.get("GRIP_LIB_PATH")
if _LIB_OVERRIDE:
    _LIB = Path(_LIB_OVERRIDE)
else:
    _LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_LIB))


def _silent_allow() -> int:
    sys.stdout.write("{}\n")
    return 0


def _emit_context(context: str) -> int:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": context,
        }
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    return 0


def _format_entries(entries) -> str:
    if not entries:
        return ""
    lines = [
        f"## Sidecar context — {len(entries)} fresh insight(s) for this file",
        "",
        "Captured by other sessions; not yet visible in the file content.",
        "",
    ]
    for e in entries:
        lines.append(
            f"- `{e.fingerprint}` [{e.classification}] — {e.hook} "
            f"(captured {e.captured_at} by {e.session_id[:8]})"
        )
    return "\n".join(lines)


def _log_error(error: Exception) -> None:
    log = Path.home() / ".claude" / "logs" / "sidecar-context-errors.jsonl"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "error": repr(error),
            "trace": traceback.format_exc(),
        }
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        return _silent_allow()
    if not raw.strip():
        return _silent_allow()
    try:
        hook_input = json.loads(raw)
    except json.JSONDecodeError:
        return _silent_allow()

    if hook_input.get("tool_name") not in ("Edit", "Write"):
        return _silent_allow()
    target_path = (hook_input.get("tool_input") or {}).get("file_path", "")
    if not target_path:
        return _silent_allow()

    try:
        from sidecar_writer import fresh_entries  # type: ignore
        entries = fresh_entries(target_path)
    except Exception as e:
        _log_error(e)
        return _silent_allow()

    if not entries:
        return _silent_allow()
    return _emit_context(_format_entries(entries))


if __name__ == "__main__":
    sys.exit(main())
