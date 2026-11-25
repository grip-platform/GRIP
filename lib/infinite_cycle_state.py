"""The never-ending cycle's shared state: small files, read and written atomically.

ADR-0016 Step 7, W1. The cycle has three moving parts that never share a
process: the Stop hook (``hooks/stop-idle-marker.py``), the watcher it arms
(``lib/infinite_cycle_runner.py``) and the reload hook that runs after a clear
(``hooks/infinite-cycle-reload.py``). They talk through the files below and
nothing else. This module is the one place those files are named, written and
read, so the three cannot drift apart. It is standard library only and cheap to
import, because the Stop hook loads it on every turn.

Two directories, deliberately distinct:

* GRIP state (``get_claude_dir()/state``): the idle markers under
  ``state/session/``, and the cycle's own files under ``state/hal-infinite/``.
* Claude Code's own directory (``$CLAUDE_CONFIG_DIR``, else ``~/.claude``),
  read only: ``sessions/<pid>.json``, where Claude Code records each running
  process's current ``sessionId``, its ``status`` (``busy`` or ``idle``) and,
  when Remote Control is on, its ``bridgeSessionId``. On this machine the two
  directories are the same path; in a worktree session ``CLAUDE_DIR`` can point
  elsewhere while Claude Code still writes to ``~/.claude``.

Keyed by the Claude Code PROCESS id, not the session id, because ``/clear``
gives the conversation a new session id while the process, its pane and its
Remote Control bridge stay the same. A hook learns that pid from ``$PPID``,
exactly as ``hooks/otty-state-hook.sh`` does, and every reader checks it against
``sessions/<pid>.json`` before trusting it.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Mapping, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from _compat import fcntl, grip_python, windows_pid_alive  # noqa: E402
from claude_dir import get_claude_dir  # noqa: E402

MARKER_DIRNAME = "session"
MARKER_PREFIX = "last-stop-"
SESSION_ID_FILENAME_LEN = 16

#: The runner the Stop hook arms, resolved from THIS checkout, so a worktree
#: launch runs the worktree's cycle and the shared install runs its own.
RUNNER = _LIB / "infinite_cycle_runner.py"


# ── paths ─────────────────────────────────────────────────────────────────

def cycle_dir(base: Optional[Path] = None) -> Path:
    """``state/hal-infinite/cycle``: handoffs, watcher locks, relief markers."""
    root = base if base is not None else get_claude_dir()
    return root / "state" / "hal-infinite" / "cycle"


def cycle_log_path(base: Optional[Path] = None) -> Path:
    """``state/hal-infinite/cycle.jsonl``: one row per cycle evaluation."""
    root = base if base is not None else get_claude_dir()
    return root / "state" / "hal-infinite" / "cycle.jsonl"


def config_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """Claude Code's own directory: ``$CLAUDE_CONFIG_DIR``, else ``~/.claude``."""
    value = str((os.environ if env is None else env).get("CLAUDE_CONFIG_DIR") or "").strip()
    return Path(value).expanduser() if value else Path.home() / ".claude"


def digest(value: object) -> str:
    """A short sha256 of an identifier, for records that must not carry it raw."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def claude_pid_arg(argv: list) -> int:
    """The ``--claude-pid`` value from a hook's argv (``"$PPID"``), or 0 if absent."""
    if "--claude-pid" not in argv:
        return 0
    at = argv.index("--claude-pid") + 1
    value = str(argv[at]) if at < len(argv) else ""
    return int(value) if value.isdigit() else 0


# ── atomic JSON ───────────────────────────────────────────────────────────

def write_json(path: Path, payload: Mapping) -> Path:
    """Write ``payload`` to ``path`` atomically: a reader sees old or new, never half.

    A torn file would parse as missing, and every reader here treats missing as
    "cannot tell", which silently stops the cycle rather than failing loudly.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # A dotfile name, so a temp file can never be mistaken for a real marker,
    # handoff or lock by a glob over the directory.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def read_json(path: Path) -> Optional[dict]:
    """A JSON object from ``path``, or ``None`` when absent, unreadable or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ── the idle marker (one per session, written on every Stop) ─────────────

def session_id(env: Optional[Mapping[str, str]] = None) -> str:
    """The session id the harness exports, or ``unknown``, truncated for a filename.

    ``CLAUDE_CODE_SESSION_ID`` is the variable Bash actually receives;
    ``CLAUDE_SESSION_ID`` is a compat fallback only, matching ``bin/grip-afk``.
    A hook prefers the id in its own stdin payload over either (see the hook).
    """
    e = os.environ if env is None else env
    for name in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        value = (e.get(name) or "").strip()
        if value:
            return value[:SESSION_ID_FILENAME_LEN]
    return "unknown"


def marker_path(*, base: Optional[Path] = None, sid: Optional[str] = None) -> Path:
    """``state/session/last-stop-<first 16 characters of the session id>.json``."""
    root = base if base is not None else get_claude_dir()
    name = (sid or session_id())[:SESSION_ID_FILENAME_LEN]
    return root / "state" / MARKER_DIRNAME / f"{MARKER_PREFIX}{name}.json"


def write_marker(path: Path, *, now: Optional[float] = None,
                 fields: Optional[Mapping] = None) -> Path:
    """Record the Stop time, plus what the watcher needs to act on this session.

    ``fields`` carries the full session id, the transcript path, the working
    directory and the Claude Code pid. ``last_stop_at`` is always set here, so
    a caller can never write a marker that lacks the one fact it exists for.
    """
    record = dict(fields or {})
    record["last_stop_at"] = time.time() if now is None else now
    record.setdefault("pid", os.getpid())
    return write_json(path, record)


def read_marker(path: Path) -> Optional[dict]:
    """The whole marker, or ``None`` when absent or unreadable."""
    return read_json(path)


def stop_time(marker: Optional[Mapping]) -> Optional[float]:
    """A marker's Stop time, or ``None`` when it is missing or not a number.

    ``None`` is the honest answer for a partial file: the watcher treats it as
    "cannot measure idleness" and waits. A fabricated timestamp could let the
    cycle clear a session that was mid-turn. A boolean is not a time, although
    Python counts it as an int.
    """
    value = (marker or {}).get("last_stop_at")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def read_last_stop(path: Path) -> Optional[float]:
    """The recorded Stop time in the marker at ``path`` (see ``stop_time``)."""
    return stop_time(read_marker(path))


# ── Claude Code's per-process session file (read only) ────────────────────

def process_session(pid: int, *, config: Optional[Path] = None) -> Optional[dict]:
    """``sessions/<pid>.json`` as Claude Code wrote it, or ``None``.

    Only the fields the cycle uses are returned, so a later reader cannot
    mistake this for a copy of the whole file.
    """
    if not isinstance(pid, int) or pid <= 1:
        return None
    data = read_json((config if config is not None else config_dir()) / "sessions" / f"{pid}.json")
    if data is None or not isinstance(data.get("sessionId"), str):
        return None
    keys = ("sessionId", "cwd", "status", "bridgeSessionId", "procStart", "kind")
    return {key: data.get(key) for key in keys}


def pid_alive(pid: int) -> bool:
    """Is a process with this pid running (and ours to signal)?

    On Windows ``os.kill(pid, 0)`` is a console Ctrl+C call, not a check
    (GRIP#8883), so the Windows PID is looked up with ``windows_pid_alive``; when
    the lookup cannot tell, the answer is True, and the watcher's ``procStart``
    comparison still ends a reused PID. Off Windows the signal probe is unchanged.
    """
    if not isinstance(pid, int) or pid <= 1:
        return False
    if sys.platform == "win32":
        try:
            return windows_pid_alive(pid)
        except OSError:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# ── the handoff: the watcher tells the reload hook what it cleared ───────

def handoff_path(pid: int, base: Optional[Path] = None) -> Path:
    return cycle_dir(base) / f"handoff-{pid}.json"


def write_handoff(pid: int, data: Mapping, base: Optional[Path] = None) -> Path:
    return write_json(handoff_path(pid, base), data)


def read_handoff(pid: int, base: Optional[Path] = None) -> Optional[dict]:
    return read_json(handoff_path(pid, base))


# ── the cycle log ─────────────────────────────────────────────────────────

def append_cycle_log(row: Mapping, base: Optional[Path] = None) -> None:
    """Append one row under an exclusive lock. Never raises.

    The log is evidence, not control: a write that fails must not stop the
    cycle, but it also must not interleave with a concurrent writer.
    """
    try:
        path = cycle_log_path(base)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.write(json.dumps(dict(row), sort_keys=True) + "\n")
    except Exception:
        pass


def read_cycle_log(base: Optional[Path] = None) -> list:
    """Every parseable row, oldest first; unreadable lines are skipped, not fatal."""
    try:
        lines = cycle_log_path(base).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# ── one watcher per Claude Code process ───────────────────────────────────

def watch_lock_path(pid: int, base: Optional[Path] = None) -> Path:
    return cycle_dir(base) / f"watch-{pid}.lock"


def try_lock(path: Path) -> Optional[int]:
    """Take an exclusive, non-blocking lock on ``path``; the fd, or ``None`` if held.

    The kernel releases the lock when its holder dies, so a crashed watcher can
    never strand the next one, and no pid file can go stale.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def watcher_running(pid: int, base: Optional[Path] = None) -> bool:
    """Does a live watcher already hold this process's lock?"""
    fd = try_lock(watch_lock_path(pid, base))
    if fd is None:
        return True
    os.close(fd)
    return False


Spawn = Callable[..., object]


def arm_watcher(pid: int, *, env: Optional[Mapping[str, str]] = None,
                spawn: Spawn = subprocess.Popen, base: Optional[Path] = None) -> str:
    """Start the watcher for Claude Code process ``pid`` unless one is running.

    Returns what happened, for the caller to record: ``armed``, ``running``
    (one already holds the lock), or ``no-runner``. The child is detached into
    its own session so it outlives this hook, and it runs under GRIP's venv
    interpreter, never a bare ``python3``, because it seals records
    (``rules/never-system-python-for-grip.md``).
    """
    if not RUNNER.is_file():
        return "no-runner"
    if watcher_running(pid, base):
        return "running"
    log = cycle_dir(base) / f"watch-{pid}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as out:
        spawn([grip_python(), str(RUNNER), "watch", "--claude-pid", str(pid)],
              stdin=subprocess.DEVNULL, stdout=out, stderr=out,
              start_new_session=True, close_fds=True,
              env=dict(os.environ if env is None else env))
    return "armed"
