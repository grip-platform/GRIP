#!/usr/bin/env python3
"""
Operation Counter Hook
======================

PreToolUse hook that counts tool operations per session and emits
periodic signals for improvement heatmap checks.

This hook:
- Increments a per-session operation counter
- Every 50 operations, triggers improvement heatmap check
- Emits [PERIODIC: PATTERN_SCAN_DUE] signal via hook output
- NEVER blocks - this is tracking, not enforcement

Version: 1.0.0
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

CLAUDE_DIR = Path.home() / ".claude"
# state_paths.SESSION_OPERATION_COUNTER and state_paths.HOOK_LOG.
COUNTER_FILE = CLAUDE_DIR / "state" / "session" / "operation-counter.json"
IMPROVEMENT_ANALYZER = CLAUDE_DIR / "lib" / "improvement-analyzer.py"
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"
CHECK_INTERVAL = 50


def log_hook(level: str, message: str):
    """Log to hook log file."""
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)  # logs/ is untracked
        with open(HOOK_LOG, "a") as f:
            f.write(f"{timestamp} [OP-COUNTER][{level}] {message}\n")
    except Exception:
        pass


def allow_response(message: str = "") -> dict:
    """Return allow response (never block)."""
    resp = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow"
        }
    }
    if message:
        resp["hookSpecificOutput"]["message"] = message
    return resp


def load_counter() -> dict:
    """Load counter state from disk."""
    try:
        if COUNTER_FILE.exists():
            return json.loads(COUNTER_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {"count": 0, "session_id": "", "last_check": 0}


def save_counter(state: dict):
    """Save counter state to disk."""
    try:
        COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)
        COUNTER_FILE.write_text(json.dumps(state))
    except OSError:
        pass


def run_heatmap_check() -> str:
    """Run improvement heatmap check, return signal if threshold hit."""
    import subprocess

    if not IMPROVEMENT_ANALYZER.exists():
        return ""

    try:
        # run_text, not subprocess.run(text=True): the heatmap block this
        # returns is rendered to the operator, box-drawing glyphs and all.
        _lib = str(CLAUDE_DIR / "lib")
        if _lib not in sys.path:
            sys.path.insert(0, _lib)
        from _compat import run_text
        result = run_text(
            ["python3", str(IMPROVEMENT_ANALYZER), "check"],
            capture_output=True,
            timeout=3
        )
        output = result.stdout.strip()
        if "[IMPROVEMENT HEATMAP]" in output:
            return output
    except Exception as e:
        log_hook("ERROR", f"Heatmap check failed: {e}")
    return ""


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps(allow_response()))
        return

    session_id = input_data.get("session_id", os.environ.get("CLAUDE_SESSION_ID", ""))
    state = load_counter()

    # Reset counter on new session
    if session_id and session_id != state.get("session_id", ""):
        state = {"count": 0, "session_id": session_id, "last_check": 0}

    state["count"] = state.get("count", 0) + 1
    count = state["count"]

    message = ""

    # Periodic check every CHECK_INTERVAL operations
    if count - state.get("last_check", 0) >= CHECK_INTERVAL:
        state["last_check"] = count
        log_hook("INFO", f"Periodic check at operation {count}")

        heatmap_signal = run_heatmap_check()
        if heatmap_signal:
            message = f"[PERIODIC: PATTERN_SCAN_DUE] Op {count}. {heatmap_signal}"
            log_hook("SIGNAL", message)
        else:
            message = f"[PERIODIC: PATTERN_SCAN_DUE] Op {count}. No heatmap signals."

    save_counter(state)
    print(json.dumps(allow_response(message)))


if __name__ == "__main__":
    main()
