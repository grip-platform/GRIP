#!/usr/bin/env python3
"""
rsi_delegate_launcher.py -- Auto-launch delegate mode for /rsi (Issue #344).

Spawns a Sonnet executor (Loop B) as a background Claude Code process,
connects via pair-mode steering, and manages its lifecycle through the
RSI wave protocol.

Delegation follows convergence depth (from the unified wave table):
  - W1 DETECTED:   Opus solo (no delegation)
  - W2 UNDERSTOOD: Opus + 1 Sonnet
  - W3 CODIFIED:   Opus + 2-3 Sonnet
  - W5 EMBODYING:  Opus + 3-5 Sonnet
  - W8 EMBODIED:   Max delegation (5-8 Sonnet)
  - Descent:       Opus creates, Sonnet supports (decreasing)

Usage:
    python3 lib/rsi_delegate_launcher.py launch [--limit 1h] [--dry-run]
    python3 lib/rsi_delegate_launcher.py status
    python3 lib/rsi_delegate_launcher.py stop

Architecture:
    /rsi --delegate (Opus planner)
        |
        +-- rsi_delegate_launcher.py launch
        |       |
        |       +-- Spawns `claude --permission-mode auto` (Sonnet, CC 2.1.76+)
        |       +-- Captures session ID from process output
        |       +-- Writes .rsi-delegate-state.json
        |
        +-- grip_pair_connect(session_id, steer=true)
        |
        +-- Wave scheduler reads wave_state.py for delegation level
        |
        +-- Monitor: git polling + delight regression detection
        |
        +-- Auto-stop on: 3 delight drops, time limit, context gate

Version: 1.0.0
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

if str(CLAUDE_DIR) not in sys.path:
    sys.path.insert(0, str(CLAUDE_DIR))
# Single shared import-safe facade — no per-callsite wrapper duplication
# (AGORA PR #2974 #2: duplicated _resolve_model would inevitably diverge).
# The facade import is itself guarded (AGORA PR #2974 #2b): if the facade
# module is absent (partial checkout / pre-merge branch), degrade to a
# verbatim-fallback resolver so this launcher still imports and the spawn
# still runs — the "never crashes" guarantee is end-to-end, not partial.
try:
    from lib.hal_model_resolver_safe import safe_resolve as _resolve_model  # noqa: E402
except Exception:  # facade unavailable — degrade, never block the launcher
    def _resolve_model(family: str, fallback: str) -> str:  # noqa: E301
        return fallback

# _process_created keeps its name here: the creation-time lookup now lives in
# _compat, shared with the keep-awake module (GRIP#8883).
from lib._compat import process_created as _process_created, windows_pid_alive  # noqa: E402

DELEGATE_STATE_FILE = CLAUDE_DIR / "state" / "rsi" / "rsi-delegate-state.json"
DELIGHT_SNAPSHOTS_FILE = CLAUDE_DIR / "learning" / "delight_snapshots.jsonl"

# Delegation capacity per wave position (max Sonnet agents)
DELEGATION_CAPACITY = {
    # (position, phase) -> max_sonnet_agents
    (1, "ascent"): 0,     # Opus solo
    (2, "ascent"): 1,     # Opus + 1 Sonnet
    (3, "ascent"): 3,     # Opus + 2-3 Sonnet
    (5, "ascent"): 5,     # Opus + 3-5 Sonnet
    (8, "ascent"): 8,     # Max delegation
    (8, "descent"): 5,    # Peak -> start descent
    (5, "descent"): 3,    # Opus creates, Sonnet supports
    (3, "descent"): 1,    # Opus writes, 1 Sonnet edits
    (2, "descent"): 0,    # Opus reflects solo
    (1, "descent"): 0,    # Opus solo, rest
}

# Delight regression: stop after N consecutive drops
DELIGHT_REGRESSION_THRESHOLD = 3

# Stall detection: no commits after N seconds
STALL_TIMEOUT_SEC = 300  # 5 minutes


@dataclass
class DelegateState:
    """State of the delegate (Loop B) process."""
    pid: int = 0
    session_id: str = ""
    started_at: str = ""
    time_limit_minutes: Optional[int] = None
    wave_position: int = 1
    wave_phase: str = "ascent"
    sonnet_capacity: int = 0
    commits_count: int = 0
    last_commit_at: str = ""
    delight_drops: int = 0
    status: str = "idle"  # idle | running | stopped | error

    @property
    def is_running(self) -> bool:
        return self.status == "running" and self.pid > 0

    @property
    def elapsed_minutes(self) -> float:
        if not self.started_at:
            return 0.0
        try:
            started = datetime.fromisoformat(self.started_at)
            delta = datetime.now(timezone.utc) - started
            return delta.total_seconds() / 60
        except (ValueError, TypeError):
            return 0.0

    @property
    def time_limit_exceeded(self) -> bool:
        if self.time_limit_minutes is None:
            return False
        return self.elapsed_minutes >= self.time_limit_minutes


def save_delegate_state(state: DelegateState) -> Path:
    """Persist delegate state to disk."""
    DELEGATE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DELEGATE_STATE_FILE.write_text(json.dumps(asdict(state), indent=2) + "\n")
    return DELEGATE_STATE_FILE


def load_delegate_state() -> Optional[DelegateState]:
    """Load delegate state from disk. Returns None if no active delegate."""
    if not DELEGATE_STATE_FILE.exists():
        return None
    try:
        data = json.loads(DELEGATE_STATE_FILE.read_text())
        return DelegateState(**data)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("[delegate] Failed to load state: %s", exc)
        return None


def clear_delegate_state() -> None:
    """Remove delegate state file."""
    if DELEGATE_STATE_FILE.exists():
        DELEGATE_STATE_FILE.unlink()
        logger.info("[delegate] Cleared delegate state")


def get_delegation_capacity(position: int, phase: str) -> int:
    """Get max Sonnet agents for a given wave position and phase.

    Returns 0 for positions where Opus works solo.
    """
    return DELEGATION_CAPACITY.get((position, phase), 0)


def parse_time_limit(limit_str: str) -> int:
    """Parse a time limit string like '1h', '30m', '2h30m' into minutes.

    Raises ValueError for invalid formats.
    """
    if not limit_str:
        raise ValueError("Empty time limit")

    total_minutes = 0
    remaining = limit_str.strip().lower()

    # Match hours
    hour_match = re.match(r"(\d+)h", remaining)
    if hour_match:
        total_minutes += int(hour_match.group(1)) * 60
        remaining = remaining[hour_match.end():]

    # Match minutes
    min_match = re.match(r"(\d+)m?$", remaining)
    if min_match:
        total_minutes += int(min_match.group(1))
        remaining = remaining[min_match.end():]

    if remaining.strip():
        raise ValueError(f"Invalid time limit format: '{limit_str}'")

    if total_minutes <= 0:
        raise ValueError(f"Time limit must be positive: '{limit_str}'")

    return total_minutes


def is_process_alive(pid: int) -> bool:
    """Check if a process is still running.

    On Windows ``os.kill(pid, 0)`` is a console Ctrl+C call, not a check
    (GRIP#8883), so the Windows PID is looked up with ``windows_pid_alive``; when
    the lookup cannot tell, the answer is True. Off Windows any OSError means the
    process has gone, as before.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            return windows_pid_alive(pid)
        except OSError:
            return True
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


_START_TOLERANCE_S = 2.0  # clock steps between process creation and the recorded start


def _delegate_identity(state: DelegateState) -> str:
    """Whether the PID this state recorded is still the delegate: "yes", "no" or "unknown".

    Off Windows this follows ``is_process_alive``, as before, so it is "yes" or "no".
    On Windows a recorded PID can name a later, unrelated process once the delegate
    has gone, after a reboot for example, and stop_delegate would then end that
    process. So there it is "yes" only when the PID belongs to a process created no
    later than ``started_at``, which the launcher records just after starting the
    delegate; "no" when no process has the PID or a later one does; and "unknown"
    when either time cannot be read (GRIP#8883).
    """
    if not is_process_alive(state.pid):
        return "no"
    if sys.platform != "win32":
        return "yes"
    try:
        started = datetime.fromisoformat(state.started_at).timestamp()
    except (TypeError, ValueError):
        return "unknown"
    created = _process_created(state.pid)
    if created is None:
        return "unknown"
    return "yes" if created <= started + _START_TOLERANCE_S else "no"


#: Keys a delight snapshot may carry its headline score under, most current
#: first. ``composite`` is the key the live writer actually emits; the two
#: older spellings are kept so a historical file still reads. Getting this
#: list wrong is invisible by construction: an unrecognised key does not
#: raise, it just yields no score, and a safety stop that sees no scores
#: reads as healthy.
DELIGHT_SCORE_KEYS = ("composite", "composite_score", "score")


def _delight_score(entry: dict) -> Optional[float]:
    """Return a snapshot's score, or None when it carries none.

    Returning None rather than 0.0 is load-bearing: a row with no
    recognised key is ABSENT data, and scoring it 0.0 would manufacture a
    drop against any preceding row — inventing a regression out of a
    schema change rather than measuring one.
    """
    for key in DELIGHT_SCORE_KEYS:
        if key in entry:
            try:
                return float(entry[key])
            except (TypeError, ValueError):
                return None
    return None


def _parse_delight_line(chunk: bytes) -> Optional[float]:
    """Decode and score one raw line; None when it yields no score."""
    if not chunk.strip():
        return None
    try:
        entry = json.loads(chunk.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return _delight_score(entry) if isinstance(entry, dict) else None


def _read_delight_scores() -> list:
    """Every score in the snapshots file, oldest first.

    Read as BYTES and decoded per line, because this file is not
    guaranteed to be valid UTF-8 — git-crypt ciphertext has been observed
    prepended to it with plaintext JSON rows appended after. A whole-file
    ``read_text()`` raises UnicodeDecodeError on the first such byte, and
    because that is a ValueError while the old handler caught only
    OSError, it escaped and took the whole stop-condition check with it.
    """
    try:
        raw = DELIGHT_SNAPSHOTS_FILE.read_bytes()
    except OSError:
        return []
    scores = []
    for chunk in raw.split(b"\n"):
        score = _parse_delight_line(chunk)
        if score is not None:
            scores.append(score)
    return scores


def _count_trailing_drops(scores: list) -> int:
    """Consecutive decreases at the tail of the series."""
    drops = 0
    for i in range(len(scores) - 1, 0, -1):
        if scores[i] < scores[i - 1]:
            drops += 1
        else:
            break
    return drops


def detect_delight_regression() -> int:
    """Count consecutive delight drops at the tail of the snapshots file.

    A "drop" is when the score decreases against the previous snapshot.
    Never raises: an unreadable file yields 0 drops, not an exception.
    """
    if not DELIGHT_SNAPSHOTS_FILE.exists():
        return 0
    scores = _read_delight_scores()
    if len(scores) < 2:
        return 0
    return _count_trailing_drops(scores)


def check_stop_conditions(state: DelegateState) -> Optional[str]:
    """Check all stop conditions for the delegate loop.

    Returns a reason string if should stop, None if should continue.
    """
    # Time limit
    if state.time_limit_exceeded:
        return f"Time limit exceeded ({state.time_limit_minutes}m)"

    # Process health
    if state.pid > 0 and not is_process_alive(state.pid):
        return f"Delegate process (PID {state.pid}) is no longer running"

    # Delight regression
    drops = detect_delight_regression()
    if drops >= DELIGHT_REGRESSION_THRESHOLD:
        return f"Delight regression detected ({drops} consecutive drops)"

    # Stall detection (no commits in STALL_TIMEOUT_SEC)
    if state.last_commit_at:
        try:
            last = datetime.fromisoformat(state.last_commit_at)
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            if elapsed > STALL_TIMEOUT_SEC:
                return f"Stall detected: no commits in {int(elapsed)}s"
        except (ValueError, TypeError):
            pass

    # RSI stop signal
    stop_signal = CLAUDE_DIR / "state" / "rsi" / "rsi-stop-signal"
    if stop_signal.exists():
        try:
            sig = json.loads(stop_signal.read_text())
            return f"RSI stop signal: {sig.get('reason', 'unknown')}"
        except (json.JSONDecodeError, OSError):
            return "RSI stop signal present"

    return None


def build_launch_command(working_dir: Optional[str] = None) -> list[str]:
    """Build the command to spawn a delegate Claude Code process.

    Uses --permission-mode auto (CC 2.1.76+) for autonomous execution.
    Falls back to --dangerously-skip-permissions for older CC versions.
    The delegate inherits GRIP safety gates via hooks regardless.
    """
    import shutil
    import subprocess

    # Detect CC version to choose the right flag
    use_auto_mode = False
    claude_path = shutil.which("claude")
    if claude_path:
        try:
            result = subprocess.run(
                ["claude", "--version"], capture_output=True, text=True, timeout=5
            )
            version_str = result.stdout.strip()
            # Parse version like "2.1.76"
            parts = version_str.split(".")
            if len(parts) >= 3:
                minor = int(parts[1]) if parts[1].isdigit() else 0
                patch = int(parts[2]) if parts[2].isdigit() else 0
                if minor > 1 or (minor == 1 and patch >= 76):
                    use_auto_mode = True
        except Exception:
            pass

    if use_auto_mode:
        cmd = ["claude", "--permission-mode", "auto",
               "--fallback-model",
               _resolve_model("anthropic/claude-sonnet-", "claude-sonnet-4-6")]
    else:
        cmd = ["claude", "--dangerously-skip-permissions"]

    if working_dir:
        cmd.extend(["--cwd", working_dir])
    return cmd


def launch(
    time_limit: Optional[str] = None,
    working_dir: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Launch a delegate (Loop B) Claude Code process.

    Args:
        time_limit: Optional time limit string (e.g., '1h', '30m').
        working_dir: Optional working directory for the delegate.
        dry_run: If True, returns the command without executing.

    Returns:
        dict with launch status, PID, and command.
    """
    # Check for existing delegate
    existing = load_delegate_state()
    if existing and existing.is_running and is_process_alive(existing.pid):
        return {
            "ok": False,
            "error": "Delegate already running",
            "pid": existing.pid,
            "session_id": existing.session_id,
        }

    # Parse time limit
    limit_minutes = None
    if time_limit:
        limit_minutes = parse_time_limit(time_limit)

    cmd = build_launch_command(working_dir)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "command": cmd,
            "time_limit_minutes": limit_minutes,
        }

    # Spawn the delegate process
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # Detach from parent
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "claude CLI not found. Ensure Claude Code is installed.",
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": f"Failed to spawn delegate: {exc}",
        }

    # Create and save state
    state = DelegateState(
        pid=proc.pid,
        session_id="",  # Will be discovered via session-lister
        started_at=datetime.now(timezone.utc).isoformat(),
        time_limit_minutes=limit_minutes,
        status="running",
    )
    save_delegate_state(state)

    logger.info(
        "[delegate] Launched Loop B (PID %d), time_limit=%s",
        proc.pid,
        f"{limit_minutes}m" if limit_minutes else "none",
    )

    return {
        "ok": True,
        "pid": proc.pid,
        "command": cmd,
        "time_limit_minutes": limit_minutes,
        "state_file": str(DELEGATE_STATE_FILE),
    }


def _not_recorded(state: DelegateState, action: str, why: str) -> dict:
    """The result of a stop that is not recorded, because the PID cannot be proven to be the delegate."""
    logger.warning("[delegate] PID %d %s; the stop is not recorded", state.pid, why)
    return {"ok": False, "action": action, "pid": state.pid, "reason": f"PID {state.pid} {why}"}


def stop_delegate(reason: str = "User requested stop") -> dict:
    """Stop the running delegate process.

    A PID that cannot be proven to be the delegate (on Windows,
    ``_delegate_identity`` is "unknown"), before the first signal or while the stop
    waits, is not marked stopped and gets no further signal; the result says so
    instead (GRIP#8883).
    """
    state = load_delegate_state()
    if not state or not state.is_running:
        return {"ok": True, "action": "no_delegate_running"}

    identity = _delegate_identity(state)
    if identity == "unknown":
        return _not_recorded(state, "not_signalled", "is running but cannot be proven to be the delegate")
    if identity == "yes":
        try:
            os.kill(state.pid, signal.SIGTERM)
            # Wait briefly for graceful shutdown
            for _ in range(10):
                identity = _delegate_identity(state)
                if identity != "yes":
                    break
                time.sleep(0.5)
            else:
                # Force kill if still alive. Windows Python has no SIGKILL, and there
                # SIGTERM already ends the process through TerminateProcess.
                os.kill(state.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except (OSError, ProcessLookupError):
            pass
        if identity == "unknown":
            return _not_recorded(state, "not_confirmed",
                                 "was sent SIGTERM but can no longer be proven to be the delegate")

    state.status = "stopped"
    save_delegate_state(state)
    logger.info("[delegate] Stopped Loop B (PID %d): %s", state.pid, reason)

    return {
        "ok": True,
        "action": "stopped",
        "pid": state.pid,
        "reason": reason,
        "elapsed_minutes": round(state.elapsed_minutes, 1),
    }


def status() -> dict:
    """Get current delegate status."""
    state = load_delegate_state()
    if not state:
        return {
            "running": False,
            "status": "no_delegate",
        }

    # Verify process is actually alive
    alive = is_process_alive(state.pid) if state.pid > 0 else False
    if state.status == "running" and not alive:
        state.status = "stopped"
        save_delegate_state(state)

    # Check stop conditions
    stop_reason = check_stop_conditions(state) if state.is_running else None

    result = asdict(state)
    result["running"] = state.is_running and alive
    result["elapsed_minutes"] = round(state.elapsed_minutes, 1)
    result["stop_reason"] = stop_reason
    result["delegation_capacity"] = get_delegation_capacity(
        state.wave_position, state.wave_phase
    )
    return result


def update_wave_position(position: int, phase: str) -> dict:
    """Update the delegate's wave position (called by wave scheduler).

    This adjusts the delegation capacity as the RSI wave progresses.
    """
    state = load_delegate_state()
    if not state:
        return {"ok": False, "error": "No delegate state"}

    state.wave_position = position
    state.wave_phase = phase
    state.sonnet_capacity = get_delegation_capacity(position, phase)
    save_delegate_state(state)

    return {
        "ok": True,
        "wave_position": position,
        "wave_phase": phase,
        "sonnet_capacity": state.sonnet_capacity,
    }


def main() -> None:
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: rsi_delegate_launcher.py <command>")
        print("Commands: launch, stop, status")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1]

    if cmd == "launch":
        limit = None
        dry_run = False
        cwd = None
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--limit" and i + 1 < len(sys.argv):
                limit = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--dry-run":
                dry_run = True
                i += 1
            elif sys.argv[i] == "--cwd" and i + 1 < len(sys.argv):
                cwd = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        result = launch(time_limit=limit, working_dir=cwd, dry_run=dry_run)
        print(json.dumps(result, indent=2))

    elif cmd == "stop":
        reason = "User requested stop"
        for i, arg in enumerate(sys.argv[2:], 2):
            if arg == "--reason" and i + 1 < len(sys.argv):
                reason = sys.argv[i + 1]
                break
        result = stop_delegate(reason)
        print(json.dumps(result, indent=2))

    elif cmd == "status":
        result = status()
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
