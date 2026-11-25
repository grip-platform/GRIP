#!/usr/bin/env python3
"""
intelligent_caffeinate.py — Prevent OS sleep during active GRIP sessions.

Cross-platform (macOS/Linux/Windows). Channel-aware — stays awake when
Discord/WhatsApp/Slack bridges are active and processing messages.

Wake reasons (any one triggers sleep prevention):
  1. Autonomous mode (RSI loop running)
  2. Channel bridge active (Discord/WhatsApp/Slack connected)
  3. RSI lock held

Each inhibitor keeps the machine awake and lets the screen sleep (V>> 2026-11-07):
caffeinate -ims on macOS, a systemd "sleep" lock on Linux, and ES_SYSTEM_REQUIRED
without ES_DISPLAY_REQUIRED on Windows. The exception is the Linux fallback
without systemd, which can only work by stopping the screen saver.

Usage:
    python3 lib/intelligent_caffeinate.py start   # Start sleep inhibition
    python3 lib/intelligent_caffeinate.py stop    # Stop
    python3 lib/intelligent_caffeinate.py status  # Check state + wake reason
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from _compat import grip_tmp_dir, process_created, windows_pid_alive  # noqa: E402
from caffeinate_keepalive import parse_etime_to_seconds  # noqa: E402

# The same record as lib/intelligent-caffeinate.sh, ${TMPDIR:-/tmp}/grip-caffeinate.pid,
# so both starters see one keep-awake. GRIP knows its keep-awake only by this file.
# On Windows a bare /tmp is \tmp on the current drive, which may not exist. So with no
# TMPDIR the record goes to the user's temp folder there. Git Bash's /tmp pointed at
# that same folder when this was measured (GRIP#9345).
PID_FILE = (Path(os.environ.get("TMPDIR") or (grip_tmp_dir() if sys.platform == "win32" else "/tmp"))
            / "grip-caffeinate.pid")
MAX_HOURS = int(os.environ.get("GRIP_CAFFEINATE_MAX_HOURS", "8"))
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
CHANNEL_PORT = int(os.environ.get("GRIP_CHANNEL_MCP_PORT", "3101"))
# caffeinate flags on macOS: -i (idle system sleep), -m (idle disk sleep) and -s
# (system sleep, on mains power). No -d or -u, so the screen still sleeps on its own
# timer (V>> 2026-11-07). The shell twin is CAFFEINATE_FLAGS in
# lib/intelligent-caffeinate.sh.
MACOS_FLAGS = "-ims"
# The macOS flags before 2026-11-07, which hold the screen on until the run expires.
LEGACY_MACOS_FLAGS = "-dimsu"
# A command line that starts with them, after an optional path to caffeinate.
_LEGACY_COMMAND = re.compile(rf"^\s*(\S*/)?caffeinate {re.escape(LEGACY_MACOS_FLAGS)}(\s|$)")
# Any caffeinate command line, after an optional path.
_CAFFEINATE_COMMAND = re.compile(r"^\s*(\S*/)?caffeinate(\s|$)")
# SetThreadExecutionState on Windows: ES_CONTINUOUS | ES_SYSTEM_REQUIRED. It leaves
# out ES_DISPLAY_REQUIRED (0x2), which would keep the screen on.
WINDOWS_EXECUTION_STATE = 0x80000001
# A record names a process that began before the record was written. A process at
# the recorded PID that began more than this many seconds after PID_FILE was
# written has reused the PID, so it is not GRIP's (GRIP#8991). The shell twin is
# _CAFF_REUSE_SLACK_S in lib/intelligent-caffeinate.sh.
REUSE_SLACK_S = 60


def _is_autonomous() -> bool:
    """Check if GRIP autonomous mode is active."""
    if os.environ.get("GRIP_AUTONOMOUS", "0") != "0":
        return True
    state_file = CLAUDE_DIR / "state" / "session" / "autonomous-state.json"
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text())
            return int(data.get("level", 0)) > 0
        except (json.JSONDecodeError, ValueError):
            pass
    return False


def _is_channel_active() -> bool:
    """Check if any Channel bridge is connected. O(1) — single HTTP GET."""
    try:
        req = urllib.request.Request(
            f"http://localhost:{CHANNEL_PORT}/health",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            # Bridges are at root level: {discord: {connected: true}, ...}
            bridge_names = ("discord", "whatsapp", "slack", "web")
            return any(
                isinstance(data.get(name), dict) and data[name].get("connected", False)
                for name in bridge_names
            )
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return False


def _is_rsi_locked() -> bool:
    """Check if RSI lock is held."""
    lock_file = CLAUDE_DIR / "tmp" / "rsi.lock"
    return lock_file.exists()


def _should_stay_awake() -> tuple:
    """Single decision point for all wake reasons. Returns (should_wake, reason)."""
    if _is_autonomous():
        return True, "autonomous"
    if _is_channel_active():
        return True, "channel"
    if _is_rsi_locked():
        return True, "rsi"
    return False, "none"


def _warmup_models() -> None:
    """Background warmup of local LLM models when channel wakes us."""
    try:
        sys.path.insert(0, str(CLAUDE_DIR))
        from lib.hal.warmup import warmup_local_models
        warmed = warmup_local_models()
        if warmed:
            print(f"[CAFFEINATE] HAL warmup: {', '.join(warmed)}")
    except ImportError:
        pass


def _command_of(pid: int) -> str:
    """The process's command line from ps, or "" when ps cannot say."""
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _age_of(pid: int):
    """Seconds since the process began, or None when that cannot be read.

    ps etime answers on macOS and Linux. Windows has no ps that GRIP can use, so
    there the creation time comes from psutil, through process_created.
    """
    if sys.platform == "win32":
        created = process_created(pid)
        return None if created is None else time.time() - created
    try:
        etime = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)],
                               capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_etime_to_seconds(etime.strip())


def _began_before_record(pid: int):
    """True when the process began before PID_FILE was written, within
    REUSE_SLACK_S; False when it began later, so its PID was reused; None when its
    start or the file cannot be read. The shell twin is _caff_began_before_record."""
    age = _age_of(pid)
    try:
        record_age = time.time() - PID_FILE.stat().st_mtime
    except OSError:
        return None
    if age is None:
        return None
    return record_age <= age + REUSE_SLACK_S


def _ps_available() -> bool:
    """Whether this machine has a ps to ask. Windows has none that GRIP can use."""
    return sys.platform != "win32" and shutil.which("ps") is not None


def _is_ours(pid: int):
    """Is the process at the recorded PID still GRIP's keep-awake?

    True when it began before PID_FILE was written and, on macOS, ps shows that it
    is a caffeinate. False when ps shows another program, or the process began
    later, so the PID was reused. None when its start cannot be read. Callers drop
    the record only on False and signal the process only on True (GRIP#8991).

    On Windows the start comes from psutil, so a reused PID is never signalled
    there, and a process whose start cannot be read is left running (GRIP#8883).
    On any other machine with no ps to ask, the record is trusted, as it was before
    this check.
    """
    if sys.platform == "win32":
        return _began_before_record(pid)
    if not _ps_available():
        return True
    known = True
    if sys.platform == "darwin":
        command = _command_of(pid).strip()
        if command and not _CAFFEINATE_COMMAND.match(command):
            return False
        known = bool(command)
    before = _began_before_record(pid)
    if before is False:
        return False
    if before is None or not known:
        return None
    return True


def _pid_alive(pid: int) -> bool:
    """Whether a process holds the recorded PID.

    A PID of 0 or less names no single process, and 0 would reach a whole process
    group, so it is never signalled or looked up. On Windows os.kill(pid, 0) is a
    console Ctrl+C call, not a check (GRIP#8883), so the PID is looked up with
    windows_pid_alive, and a lookup that fails reads as alive: a record kept too
    long ends with the keep-awake's own limit, but a dropped record leaves a
    keep-awake that nothing can stop. Off Windows any OSError means the process has
    gone, as before.
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
    except OSError:
        return False
    return True


def _read_pid():
    """Read PID from file, return None if stale or missing.

    The recorded PID must still be GRIP's keep-awake (_is_ours). A PID that runs
    another program, or a process that began after the record was written, was
    reused: its record is dropped, and it is never reported or stopped. When the
    start of the process cannot be read, the record is kept (GRIP#8991).
    """
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        pid = 0
    if not _pid_alive(pid) or _is_ours(pid) is False:
        PID_FILE.unlink(missing_ok=True)
        return None
    return pid


def _retire_legacy_inhibitor() -> bool:
    """Stop the recorded keep-awake if it still runs with the old macOS flags.

    Returns True when it stopped one, and start() then spawns the replacement even
    without a wake reason, because whoever started the old one may still need the
    machine awake. Only the PID in PID_FILE is checked, and it is signalled only
    when ps confirms it is GRIP's (_is_ours), so a caffeinate someone else started,
    or one that reused the PID, is never touched.
    """
    pid = _read_pid()
    if pid is None or sys.platform != "darwin" or _is_ours(pid) is not True:
        return False
    if not _LEGACY_COMMAND.match(_command_of(pid)):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    PID_FILE.unlink(missing_ok=True)
    return True


def _windows_inhibitor_command(max_sec: int) -> str:
    """The PowerShell text that holds Windows awake for ``max_sec`` seconds.

    It calls SetThreadExecutionState on its own thread and then sleeps on that
    thread, so the request ends when the process ends. The state is passed as a
    typed decimal: Windows PowerShell 5.1 reads the hex literal 0x80000001 as a
    negative 32-bit number and refuses it as a uint. When the type cannot be
    built, or the call returns 0, the process exits at once with code 1, so a
    keep-awake that could not ask Windows does not stay alive as if it had
    (GRIP#9345).
    """
    return (
        "$ErrorActionPreference = 'Stop'; "
        "$api = Add-Type -MemberDefinition '[DllImport(\"kernel32.dll\")] "
        "public static extern uint SetThreadExecutionState(uint esFlags);' "
        "-Name GripKeepAwake -Namespace GripWin32 -PassThru; "
        f"if ($api::SetThreadExecutionState([uint32]{WINDOWS_EXECUTION_STATE}) -eq 0) {{ exit 1 }}; "
        f"Start-Sleep -Seconds {max_sec}"
    )


def _spawn_inhibitor(max_sec: int):
    """Spawn platform-specific sleep inhibitor process."""
    devnull = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    plat = sys.platform
    if plat == "darwin":
        return subprocess.Popen(["caffeinate", MACOS_FLAGS, "-t", str(max_sec)], **devnull)
    if plat == "linux" and _cmd_exists("systemd-inhibit"):
        # "sleep" alone holds the machine up. An "idle" lock also stops the
        # desktop's idle detection, which on some desktops keeps the screen on.
        return subprocess.Popen(
            ["systemd-inhibit", "--what=sleep", "--who=GRIP",
             "--why=Channel bridge active", "sleep", str(max_sec)], **devnull)
    if plat == "linux":
        # Without systemd there is no portable way to hold the machine alone,
        # so this fallback also stops the screen saver.
        return subprocess.Popen(
            ["bash", "-c", f"for i in $(seq 1 {max_sec // 60}); do "
             f"xdg-screensaver reset 2>/dev/null; sleep 60; done"], **devnull)
    if plat == "win32":
        return subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
             _windows_inhibitor_command(max_sec)], **devnull)
    return None


def _record_or_end(proc):
    """Write PID_FILE for the keep-awake just started, or end it and say why.

    GRIP knows its keep-awake only by this record, so a process it could not
    record could be neither seen nor stopped, and would hold the machine awake
    until its own limit (GRIP#9354). Returns None when the record was written.
    """
    try:
        PID_FILE.write_text(str(proc.pid))
        return None
    except OSError as exc:
        reason = exc.strerror or str(exc)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return f"not started: its record {PID_FILE} could not be written ({reason})"


def start() -> str:
    """Start sleep inhibition based on wake reason."""
    replaced = _retire_legacy_inhibitor()
    should_wake, reason = _should_stay_awake()
    if not should_wake and not replaced:
        return "skipped (no wake reason)"
    if not should_wake:
        reason = "replacing a keep-awake that held the screen on"
    if _read_pid():
        return f"already running (PID: {PID_FILE.read_text().strip()}, reason: {reason})"
    if reason == "channel":
        threading.Thread(target=_warmup_models, daemon=True).start()
    proc = _spawn_inhibitor(MAX_HOURS * 3600)
    if not proc:
        return f"unsupported platform: {sys.platform}"
    failed = _record_or_end(proc)
    if failed:
        return failed
    # The platform was reported until de3ddeeb6 (2026-04-04) moved the spawn code into
    # _spawn_inhibitor and the field went with it; that commit only meant to refactor.
    return f"started (PID: {proc.pid}, reason: {reason}, max: {MAX_HOURS}h, platform: {sys.platform})"


def stop() -> str:
    """Stop sleep inhibition.

    The recorded process is signalled only when it is confirmed as GRIP's
    (_is_ours). When that cannot be confirmed, it is left running with its
    record, and it ends at its own limit (GRIP#8991).
    """
    pid = _read_pid()
    if not pid:
        PID_FILE.unlink(missing_ok=True)
        return "not running"
    if not _is_ours(pid):
        return f"left running (PID: {pid}): it could not be confirmed as GRIP's keep-awake"
    try:
        os.kill(pid, signal.SIGTERM)
        result = f"stopped (PID: {pid})"
    except OSError:
        result = f"PID {pid} already exited"
    PID_FILE.unlink(missing_ok=True)
    return result


def status() -> str:
    """Check current state with wake reason."""
    pid = _read_pid()
    should_wake, reason = _should_stay_awake()
    if pid:
        return f"active (PID: {pid}, wake reason: {reason})"
    if should_wake:
        return f"inactive but SHOULD be active (reason: {reason} — run 'start')"
    return "inactive (no wake reason)"


def _cmd_exists(cmd: str) -> bool:
    """Check if a command exists on PATH."""
    try:
        subprocess.run(["which", cmd], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        print(f"[CAFFEINATE] {start()}")
    elif cmd == "stop":
        print(f"[CAFFEINATE] {stop()}")
    elif cmd == "status":
        print(f"[CAFFEINATE] {status()}")
    else:
        print(f"Usage: {sys.argv[0]} [start|stop|status]")
        sys.exit(1)
