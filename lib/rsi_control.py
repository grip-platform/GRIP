#!/usr/bin/env python3
"""
rsi_control.py -- Remote RSI loop control (Issue #57, Phase 1-2).

Provides start/stop/status/schedule commands for RSI autonomous sessions.
The stop signal is read by Gate $ (context-gate-hook.py) on every tool call,
providing immediate halt capability without process signals.

Usage:
    python3 lib/rsi_control.py stop --reason "Done for today"
    python3 lib/rsi_control.py stop --reason "Budget concern" --by U0EXAMPLE01
    python3 lib/rsi_control.py clear              # Remove stop signal
    python3 lib/rsi_control.py status              # Show current RSI state
    python3 lib/rsi_control.py schedule --focus "test coverage" --budget 50.0

The stop signal file (.rsi-stop-signal) is checked by Gate $ before every
tool call in autonomous mode. Writing this file halts the RSI loop within
one tool call cycle (~seconds).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
STOP_SIGNAL = CLAUDE_DIR / "state" / "rsi" / "rsi-stop-signal"
SCHEDULE_FILE = CLAUDE_DIR / "state" / "rsi" / "rsi-schedule.json"
CONVERGE_STATE = CLAUDE_DIR / "state" / "rsi" / "auto-converge-state.json"


def stop(reason: str = "User requested stop", requested_by: str = "manual") -> dict:
    """Write stop signal file. Gate $ will halt the RSI loop on next tool call."""
    signal = {
        "reason": reason,
        "requested_by": requested_by,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    STOP_SIGNAL.write_text(json.dumps(signal, indent=2))
    return {"ok": True, "signal": signal, "file": str(STOP_SIGNAL)}


def clear() -> dict:
    """Remove stop signal to allow RSI loops to resume."""
    if STOP_SIGNAL.exists():
        STOP_SIGNAL.unlink()
        return {"ok": True, "action": "cleared"}
    return {"ok": True, "action": "no_signal_present"}


def status() -> dict:
    """Collect current RSI control state from all signal files."""
    result = {
        "stop_signal": None,
        "schedule": None,
        "auto_converge": None,
    }

    if STOP_SIGNAL.exists():
        try:
            result["stop_signal"] = json.loads(STOP_SIGNAL.read_text())
        except (json.JSONDecodeError, OSError):
            result["stop_signal"] = {"error": "corrupt signal file"}

    if SCHEDULE_FILE.exists():
        try:
            result["schedule"] = json.loads(SCHEDULE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    if CONVERGE_STATE.exists():
        try:
            cs = json.loads(CONVERGE_STATE.read_text())
            result["auto_converge"] = {
                "cycles_today": cs.get("cycles_today", 0),
                "total_cycles": cs.get("total_cycles", 0),
                "best_score": cs.get("best_score", 0.0),
            }
        except (json.JSONDecodeError, OSError):
            pass

    result["running"] = result["stop_signal"] is None
    return result


def schedule(
    focus: str = "general improvements",
    max_hours: float = 4.0,
    notify_slack_user: str = "",
) -> dict:
    """Write RSI schedule config for launchd/cron pickup."""
    config = {
        "enabled": True,
        "focus": focus,
        "max_duration_hours": max_hours,
        "notify_slack_user": notify_slack_user,
        "notify_on": ["start", "complete", "error"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    SCHEDULE_FILE.write_text(json.dumps(config, indent=2))
    return {"ok": True, "schedule": config}


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: rsi_control.py <command>")
        print("Commands: stop, clear, status, schedule")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "stop":
        reason = "User requested stop"
        by = "manual"
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--reason" and i + 1 < len(sys.argv):
                reason = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--by" and i + 1 < len(sys.argv):
                by = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        result = stop(reason, by)
        print(f"[RSI] Stop signal written: {result['signal']['reason']}")

    elif cmd == "clear":
        result = clear()
        print(f"[RSI] {result['action']}")

    elif cmd == "status":
        result = status()
        print(json.dumps(result, indent=2))

    elif cmd == "schedule":
        focus = "general improvements"
        hours = 4.0
        slack_user = ""
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--focus" and i + 1 < len(sys.argv):
                focus = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--hours" and i + 1 < len(sys.argv):
                hours = float(sys.argv[i + 1])
                i += 2
            elif sys.argv[i] == "--notify" and i + 1 < len(sys.argv):
                slack_user = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        result = schedule(focus, hours, slack_user)
        print(f"[RSI] Schedule written: {result['schedule']['focus']}")
        print(f"[RSI] Max: {hours}h")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
