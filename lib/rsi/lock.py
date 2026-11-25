#!/usr/bin/env python3
"""RSI session lock — prevent concurrent RSI loops, caffeinate integration."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from _compat import windows_pid_alive

from . import config
from .metrics import detect_session_type, record_metric


def _caffeinate_start_for_rsi():
    """Start caffeinate for RSI duration (macOS only, idempotent)."""
    caff_script = config.CLAUDE_DIR / "lib" / "intelligent-caffeinate.sh"
    if not caff_script.exists():
        return
    try:
        subprocess.run(
            ["bash", "-c", f'source "{caff_script}" && caffeinate_start_for_rsi'],
            timeout=5, capture_output=True
        )
    except Exception:
        pass


def _caffeinate_stop():
    """Stop caffeinate after RSI completes."""
    caff_script = config.CLAUDE_DIR / "lib" / "intelligent-caffeinate.sh"
    if not caff_script.exists():
        return
    try:
        subprocess.run(
            ["bash", "-c", f'source "{caff_script}" && caffeinate_stop'],
            timeout=5, capture_output=True
        )
    except Exception:
        pass


def _owner_alive(pid: int) -> bool:
    """True while the process that took the lock still runs.

    The lock records that process's ``os.getpid()``. On Windows
    ``os.kill(pid, 0)`` sends Ctrl+C to a console process group instead of
    checking anything (GRIP#8883), so the Windows PID is looked up with
    ``windows_pid_alive``. When it cannot tell, without psutil or because the
    lookup itself fails with OSError, the answer is True, which keeps the lock;
    the lock still expires after ``config.RSI_LOCK_STALE_SECONDS``. Off Windows
    any OSError means the owner has gone, as before.
    """
    if sys.platform == "win32":
        try:
            return windows_pid_alive(pid)
        except OSError:
            return True
    try:
        os.kill(pid, 0)  # Signal 0 = check existence
    except OSError:
        return False
    return True


def _held_by_live_owner(lock_data: dict) -> str:
    """Why a live RSI session still holds the lock, or "" when it may be taken.

    A lock older than ``config.RSI_LOCK_STALE_SECONDS``, a lock with no PID and
    a lock whose owner has ended are removed here. A lock with no timestamp is
    left for the caller to overwrite.
    """
    lock_pid = lock_data.get("pid", 0)
    lock_time = lock_data.get("timestamp", "")
    if not lock_time:
        return ""
    lock_dt = datetime.fromisoformat(lock_time)
    age_seconds = (datetime.now(timezone.utc) - lock_dt).total_seconds()
    if age_seconds > config.RSI_LOCK_STALE_SECONDS:
        config.RSI_LOCK_FILE.unlink(missing_ok=True)
        record_metric("rsi_lock_stale_cleaned", {
            "stale_pid": lock_pid,
            "age_seconds": age_seconds,
        })
        return ""
    if not lock_pid:
        config.RSI_LOCK_FILE.unlink(missing_ok=True)
        return ""
    if _owner_alive(lock_pid):
        return (
            f"RSI lock held by PID {lock_pid} "
            f"(session: {lock_data.get('session_id', '?')}, "
            f"age: {age_seconds:.0f}s)"
        )
    config.RSI_LOCK_FILE.unlink(missing_ok=True)
    record_metric("rsi_lock_orphan_cleaned", {"dead_pid": lock_pid})
    return ""


def acquire_rsi_lock(session_id: str = "") -> tuple[bool, str]:
    """Acquire exclusive RSI lock. Returns (success, message).

    Creates .rsi-active.lock with PID and session ID.
    Fails if another RSI session holds the lock (and is still alive).
    Auto-cleans stale locks older than config.RSI_LOCK_STALE_SECONDS.

    Args:
        session_id: Optional session identifier for debugging.

    Returns:
        (True, "acquired") on success.
        (False, "reason") if another RSI session is active.
    """
    if config.RSI_LOCK_FILE.exists():
        try:
            held = _held_by_live_owner(json.loads(config.RSI_LOCK_FILE.read_text()))
        except (json.JSONDecodeError, OSError, ValueError):
            # Corrupt lock file — clean up
            config.RSI_LOCK_FILE.unlink(missing_ok=True)
        else:
            if held:
                return False, held

    # Acquire the lock
    lock_data = {
        "pid": os.getpid(),
        "session_id": session_id or detect_session_type(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_type": detect_session_type(),
    }
    config.RSI_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.RSI_LOCK_FILE.write_text(json.dumps(lock_data, indent=2))
    record_metric("rsi_lock_acquired", {"pid": os.getpid()})
    _caffeinate_start_for_rsi()
    return True, "acquired"


def release_rsi_lock() -> bool:
    """Release the RSI lock. Returns True if released, False if not held.

    Only releases if the lock belongs to the current process (PID check).
    """
    if not config.RSI_LOCK_FILE.exists():
        return False

    try:
        lock_data = json.loads(config.RSI_LOCK_FILE.read_text())
        lock_pid = lock_data.get("pid", 0)

        # Only release our own lock
        if lock_pid != os.getpid():
            return False

        config.RSI_LOCK_FILE.unlink(missing_ok=True)
        record_metric("rsi_lock_released", {"pid": os.getpid()})
        _caffeinate_stop()
        return True
    except (json.JSONDecodeError, OSError):
        # Corrupt — try to clean up
        config.RSI_LOCK_FILE.unlink(missing_ok=True)
        return True


def check_rsi_lock() -> dict:
    """Check current RSI lock status without modifying it.

    Returns:
        Dict with: locked (bool), pid, session_id, age_seconds, stale.
    """
    if not config.RSI_LOCK_FILE.exists():
        return {"locked": False}

    try:
        lock_data = json.loads(config.RSI_LOCK_FILE.read_text())
        lock_pid = lock_data.get("pid", 0)
        lock_time = lock_data.get("timestamp", "")

        age_seconds = 0.0
        if lock_time:
            lock_dt = datetime.fromisoformat(lock_time)
            age_seconds = (datetime.now(timezone.utc) - lock_dt).total_seconds()

        # Check if process is alive
        alive = bool(lock_pid) and _owner_alive(lock_pid)

        return {
            "locked": True,
            "pid": lock_pid,
            "session_id": lock_data.get("session_id", ""),
            "session_type": lock_data.get("session_type", ""),
            "age_seconds": age_seconds,
            "process_alive": alive,
            "stale": age_seconds > config.RSI_LOCK_STALE_SECONDS or not alive,
        }
    except (json.JSONDecodeError, OSError, ValueError):
        return {"locked": True, "stale": True, "error": "corrupt_lock"}
