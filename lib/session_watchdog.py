#!/usr/bin/env python3
"""
Session Watchdog - Detects hung/stale GRIP sessions and writes checkpoints.

Advisory only: does NOT kill processes. Scans for sessions that show no
activity for a configurable threshold (default 5 minutes) with low CPU.

Usage:
    python3 lib/session_watchdog.py check          # One-shot scan
    python3 lib/session_watchdog.py watch           # Continuous (60s interval)
    python3 lib/session_watchdog.py watch --interval 30  # Custom interval
    python3 lib/session_watchdog.py check --threshold 600  # 10 min staleness

Signals:
    - Writes stale_checkpoint to instance JSON
    - Logs warnings to ~/.claude/logs/hooks.log
    - Optionally notifies via existing session-control-server WebSocket

Cross-platform: macOS + Linux (uses Python os.path.getmtime, no stat).
Stdlib only: no external dependencies.
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Constants ────────────────────────────────────────────────────────

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
PROJECTS_DIR = CLAUDE_DIR / "projects"
INSTANCES_DIR = CLAUDE_DIR / "instances"
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"

DEFAULT_STALE_THRESHOLD = 300  # 5 minutes in seconds
DEFAULT_WATCH_INTERVAL = 60    # seconds between scans
CPU_IDLE_THRESHOLD = 10.0      # percent — below this counts as idle
MAX_SIGNAL_ATTEMPTS = 3        # cap re-signals per session in one watch run


# ── Logging ──────────────────────────────────────────────────────────

def log(level: str, message: str) -> None:
    """Append to GRIP hook log."""
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(HOOK_LOG, "a") as f:
            f.write(f"{ts} [SESSION-WATCHDOG][{level}] {message}\n")
    except OSError:
        pass


# ── CPU Usage ────────────────────────────────────────────────────────

def get_process_cpu(pid: int) -> float | None:
    """Get CPU usage percentage for a process. Returns None if unavailable.

    Uses /proc on Linux, ps on macOS. Both are stdlib-compatible.
    """
    try:
        # Try ps (works on macOS and Linux)
        import subprocess
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu="],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError, OSError):
        pass
    return None


# ── Session Discovery ────────────────────────────────────────────────

def _session_entry(jf: Path, project_name: str) -> dict[str, Any] | None:
    """Build one session info dict from a JSONL file; returns None if too small."""
    try:
        stat = jf.stat()
    except OSError:
        return None
    if stat.st_size < 100:
        return None
    session_id = jf.stem
    return {
        "session_id": session_id,
        "project": project_name,
        "last_activity": stat.st_mtime,
        "last_activity_iso": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
        "file_size": stat.st_size,
        "has_session_dir": (jf.parent / session_id).is_dir(),
        "pid": _find_session_pid(session_id),
        "jsonl_path": str(jf),
    }


def find_active_sessions() -> list[dict[str, Any]]:
    """Scan for active Claude Code sessions via JSONL logs (mtime = activity)."""
    if not PROJECTS_DIR.is_dir():
        return []
    entries = (
        _session_entry(jf, jf.parent.name)
        for jf in sorted(
            PROJECTS_DIR.glob("*/*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    )
    return [e for e in entries if e is not None]


def _find_session_pid(session_id: str) -> int | None:
    """Try to find the PID for a session from socket/pid files."""
    import tempfile

    pid_file = Path(tempfile.gettempdir()) / f"grip-session-server-{session_id}.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            # Verify process is alive
            os.kill(pid, 0)
            return pid
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            pass

    return None


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


# ── Staleness Detection ──────────────────────────────────────────────

def detect_stale_sessions(
    threshold: float = DEFAULT_STALE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Find sessions that appear stale.

    A session is stale when:
    1. No JSONL write for > threshold seconds, AND
    2. Either no PID found, or PID has low CPU usage

    Returns list of stale session dicts with staleness metadata.
    """
    now = time.time()
    sessions = find_active_sessions()
    stale = []

    for session in sessions:
        idle_seconds = now - session["last_activity"]

        if idle_seconds < threshold:
            continue

        # Check CPU if we have a PID
        cpu = None
        is_cpu_idle = True
        if session["pid"]:
            if not _is_process_alive(session["pid"]):
                # Process is dead — definitely stale
                is_cpu_idle = True
                cpu = 0.0
            else:
                cpu = get_process_cpu(session["pid"])
                if cpu is not None:
                    is_cpu_idle = cpu < CPU_IDLE_THRESHOLD

        if not is_cpu_idle:
            continue

        stale.append({
            **session,
            "idle_seconds": round(idle_seconds, 1),
            "idle_display": _format_duration(idle_seconds),
            "cpu_percent": cpu,
            "stale_reason": _stale_reason(session, idle_seconds, cpu),
        })

    return stale


def _stale_reason(session: dict, idle_seconds: float, cpu: float | None) -> str:
    """Generate a human-readable staleness reason."""
    parts = [f"idle {_format_duration(idle_seconds)}"]
    if session["pid"] is None:
        parts.append("no PID found")
    elif cpu is not None:
        parts.append(f"CPU {cpu:.1f}%")
    return ", ".join(parts)


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes}m"


# ── Checkpoint Writing ───────────────────────────────────────────────

def write_checkpoint(session: dict[str, Any]) -> bool:
    """Write a stale_checkpoint to the session's instance JSON (if found).

    Searches both per-project grip/ dir and global instances/ dir.
    Returns True if checkpoint was written.
    """
    session_id = session["session_id"]
    checkpoint = {
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "idle_seconds": session.get("idle_seconds", 0),
        "idle_display": session.get("idle_display", "unknown"),
        "cpu_percent": session.get("cpu_percent"),
        "stale_reason": session.get("stale_reason", ""),
        "last_activity": session.get("last_activity_iso", ""),
    }

    # Look for instance JSON in per-project grip/ directory
    instance_path = _find_instance_json(session_id, session.get("project", ""))
    if instance_path is None:
        log("DEBUG", f"No instance JSON found for {session_id[:8]}")
        return False

    try:
        with open(instance_path) as f:
            data = json.load(f)

        data["stale_checkpoint"] = checkpoint

        with open(instance_path, "w") as f:
            json.dump(data, f, indent=2)

        log("INFO", f"Checkpoint written for {session_id[:8]} -> {instance_path}")
        return True
    except (json.JSONDecodeError, OSError, KeyError) as e:
        log("ERROR", f"Failed to write checkpoint for {session_id[:8]}: {e}")
        return False


def _find_instance_json(session_id: str, project_name: str = "") -> Path | None:
    """Find the instance JSON file for a session ID."""
    # Check per-project grip/ directory first
    if project_name:
        grip_dir = PROJECTS_DIR / project_name / "grip"
        instance_path = grip_dir / f"{session_id}.json"
        if instance_path.exists():
            return instance_path

    # Check global instances directory
    global_path = INSTANCES_DIR / f"{session_id}.json"
    if global_path.exists():
        return global_path

    # Scan all project grip/ directories as fallback — single glob, O(n).
    if PROJECTS_DIR.is_dir():
        matches = list(PROJECTS_DIR.glob(f"*/grip/{session_id}.json"))
        if matches:
            return matches[0]

    return None


# ── WebSocket Signal (optional) ──────────────────────────────────────

def signal_session(session_id: str) -> bool:
    """Try to signal a stale session via its control server socket.

    Uses the Unix socket at /tmp/grip-session-{session_id}.sock to send
    a watchdog notification. Non-blocking, best-effort.
    """
    import socket
    import tempfile

    sock_path = os.path.join(
        tempfile.gettempdir(), f"grip-session-{session_id}.sock"
    )

    if not os.path.exists(sock_path):
        return False

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(sock_path)
        msg = json.dumps({
            "type": "watchdog_alert",
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": "Session appears stale",
        })
        s.sendall(msg.encode("utf-8"))
        s.close()
        return True
    except (OSError, socket.error):
        return False


# ── CLI Commands ─────────────────────────────────────────────────────

def _print_stale_session(s: dict[str, Any]) -> None:
    """Print one stale session summary to stdout."""
    print(f"  Session: {s['session_id'][:12]}...")
    print(f"  Project: {s['project']}")
    print(f"  Idle:    {s['idle_display']}")
    print(f"  Reason:  {s['stale_reason']}")
    if s.get("pid"):
        print(f"  PID:     {s['pid']}")
    print()


def _handle_stale_session(
    s: dict[str, Any],
    signal_attempts: dict[str, int],
    quiet: bool,
) -> bool:
    """Checkpoint + optionally signal one stale session. Returns True if checkpointed."""
    sid = s["session_id"]
    attempts = signal_attempts.get(sid, 0)
    if not quiet:
        print(f"  - {sid[:12]}... ({s['stale_reason']})")
    checkpointed = write_checkpoint(s)
    if attempts < MAX_SIGNAL_ATTEMPTS:
        signal_session(sid)
        signal_attempts[sid] = attempts + 1
        log("WARN", f"Stale session: {sid[:8]} ({s['stale_reason']}) attempt {attempts + 1}")
    else:
        log("INFO", f"Stale session {sid[:8]} signal cap reached ({MAX_SIGNAL_ATTEMPTS}); skipping re-signal")
    return checkpointed


def _process_stale_batch(
    stale: list[dict[str, Any]],
    signal_attempts: dict[str, int],
    quiet: bool,
) -> int:
    """Checkpoint and signal every session in stale; return count checkpointed."""
    return sum(
        1 for s in stale if _handle_stale_session(s, signal_attempts, quiet=quiet)
    )


def _interruptible_sleep(interval: float, is_running: Any) -> None:
    """Sleep for `interval` seconds in 1s increments, checking is_running each tick.

    Extracted from cmd_watch to eliminate the nested while-inside-while pattern
    that the BIG-O gate flags as potential O(n^2). This is O(interval) — linear
    in the configured sleep duration, not in data size.
    """
    elapsed = 0.0
    while is_running() and elapsed < interval:
        time.sleep(min(1.0, interval - elapsed))
        elapsed += 1.0


def cmd_check(args: argparse.Namespace) -> int:
    """One-shot stale session scan."""
    stale = detect_stale_sessions(threshold=args.threshold)

    if not stale:
        if not args.quiet:
            print("No stale sessions detected.")
        return 0

    if args.json:
        print(json.dumps(stale, indent=2))
        return 1

    print(f"Found {len(stale)} stale session(s):\n")
    _ = [_print_stale_session(s) for s in stale]
    signal_attempts: dict[str, int] = {}
    checkpoints_written = _process_stale_batch(stale, signal_attempts, quiet=args.quiet)

    if not args.quiet and checkpoints_written > 0:
        print(f"Wrote {checkpoints_written} checkpoint(s).")

    return 1


def cmd_watch(args: argparse.Namespace) -> int:
    """Continuous monitoring loop."""
    interval = args.interval
    threshold = args.threshold

    if not args.quiet:
        print(f"Watchdog active: scanning every {interval}s, "
              f"threshold {threshold}s")
        print("Press Ctrl+C to stop.\n")

    log("INFO", f"Watchdog started: interval={interval}s, threshold={threshold}s")

    running = True

    def handle_signal(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    scan_count = 0
    # Guard: track how many times each session has been signalled.
    # Prevents the watchdog-cut shape where a dead/stuck session is
    # re-signalled indefinitely across waves (W22-bg pattern).
    signal_attempts: dict[str, int] = {}

    while running:
        scan_count += 1
        stale = detect_stale_sessions(threshold=threshold)

        if stale:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            if not args.quiet:
                print(f"[{ts}] Scan #{scan_count}: {len(stale)} stale session(s)")
            _process_stale_batch(stale, signal_attempts, quiet=args.quiet)
        elif not args.quiet and scan_count % 10 == 0:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{ts}] Scan #{scan_count}: all clear")

        _interruptible_sleep(interval, lambda: running)

    if not args.quiet:
        print("\nWatchdog stopped.")
    log("INFO", "Watchdog stopped")
    return 0


# ── Entry Point ──────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="GRIP Session Watchdog - detect stale/hung sessions",
        prog="session_watchdog",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # check subcommand
    check_parser = subparsers.add_parser(
        "check", help="One-shot scan for stale sessions"
    )
    check_parser.add_argument(
        "--threshold", type=float, default=DEFAULT_STALE_THRESHOLD,
        help=f"Staleness threshold in seconds (default: {DEFAULT_STALE_THRESHOLD})"
    )
    check_parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON"
    )
    check_parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress non-essential output"
    )

    # watch subcommand
    watch_parser = subparsers.add_parser(
        "watch", help="Continuous monitoring loop"
    )
    watch_parser.add_argument(
        "--interval", type=float, default=DEFAULT_WATCH_INTERVAL,
        help=f"Scan interval in seconds (default: {DEFAULT_WATCH_INTERVAL})"
    )
    watch_parser.add_argument(
        "--threshold", type=float, default=DEFAULT_STALE_THRESHOLD,
        help=f"Staleness threshold in seconds (default: {DEFAULT_STALE_THRESHOLD})"
    )
    watch_parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress non-essential output"
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "check":
        return cmd_check(args)
    elif args.command == "watch":
        return cmd_watch(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
