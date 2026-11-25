"""GRIP-layer advisory git mutex using fcntl.flock.

This module is a LAYER ABOVE git's own `.git/index.lock`. It serialises
GRIP callers (closeout, grip_save, saving-state, hooks) so that only one
GRIP consumer reaches the git command-line at a time. Git's own lock
still exists; this module just ensures we don't race ourselves into it.

Key properties git's lock lacks:
- OS-level ownership via fcntl.flock (released automatically on process death)
- Blocking acquire with timeout (queue semantics instead of hard fail)
- Owner metadata (PID, session, operation, reason) queryable by any process
- Per-git-dir granularity so separate repos do not block each other

Usage:
    from lib.git_lock import grip_git_lock, GitLockTimeout, current_holder

    with grip_git_lock(operation="commit", reason="closeout"):
        subprocess.run(["git", "add", "-u"])
        subprocess.run(["git", "commit", "-m", "..."])

    holder = current_holder()   # None or dict with pid/operation/acquired_at
"""

import errno
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator, Optional
try:
    from _compat import fcntl  # POSIX fcntl, or the LockFileEx lock on Windows
except ImportError:  # imported/run as lib.X, so lib/ is not itself on sys.path
    from lib._compat import fcntl

__all__ = [
    "grip_git_lock",
    "GitLockTimeout",
    "GitLockError",
    "LockMetadata",
    "current_holder",
    "is_locked",
    "lock_paths",
]


class GitLockError(RuntimeError):
    """Base class for GRIP git-lock errors."""


class GitLockTimeout(GitLockError):
    """Raised when grip_git_lock cannot acquire within the deadline.

    The instance carries a snapshot of the holder's metadata (if readable)
    so callers can report who was in the way.
    """

    def __init__(self, holder: Optional["LockMetadata"], timeout: float):
        self.holder = holder
        self.timeout = timeout
        if holder is None:
            super().__init__(
                f"grip git lock still held after {timeout:.1f}s; holder unknown"
            )
        else:
            super().__init__(
                f"grip git lock still held after {timeout:.1f}s by "
                f"pid={holder.pid} op={holder.operation!r} age={holder.age_seconds:.1f}s"
            )


@dataclass
class LockMetadata:
    """Human-readable information about the current lock holder."""

    pid: int
    hostname: str
    session_id: str
    operation: str
    reason: str
    acquired_at: float
    agent: str

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.acquired_at)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["age_seconds"] = round(self.age_seconds, 3)
        return d


def _git_dir(cwd: Optional[Path] = None) -> Path:
    """Resolve the *common* git directory for the given cwd (or $PWD).

    Uses `git rev-parse --git-common-dir`, NOT `--git-dir`. This matters for
    linked worktrees: each worktree has its own `--git-dir`
    (.git/worktrees/<name>/), but all worktrees share the same
    `--git-common-dir` (the main .git/). Anchoring the lock on the common
    dir ensures a main-tree session and a worktree session acquire the
    SAME grip-lock and therefore serialise correctly against each other.

    `--git-common-dir` is the same as `--git-dir` in a plain (non-worktree)
    repo, so this is strictly safer with no downside.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise GitLockError(f"not inside a git repository: {e}") from e

    raw = result.stdout.strip()
    path = Path(raw)
    if not path.is_absolute():
        base = Path(cwd) if cwd else Path.cwd()
        path = (base / path).resolve()
    return path


def lock_paths(cwd: Optional[Path] = None) -> tuple[Path, Path]:
    """Return (fcntl_path, metadata_path) for the current git dir.

    The fcntl file is the low-level ownership token (locked by flock).
    The metadata file is a sibling JSON file holding holder info.
    They are separate to avoid size-0 race conditions.
    """
    gd = _git_dir(cwd)
    return gd / "grip-lock", gd / "grip-lock.json"


def _session_id() -> str:
    return (
        os.environ.get("GRIP_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or ""
    )


def _agent_name() -> str:
    return os.environ.get("GRIP_AGENT", "claude-code")


def _read_metadata(meta_path: Path) -> Optional[LockMetadata]:
    try:
        raw = meta_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    try:
        return LockMetadata(
            pid=int(data["pid"]),
            hostname=str(data.get("hostname", "")),
            session_id=str(data.get("session_id", "")),
            operation=str(data.get("operation", "")),
            reason=str(data.get("reason", "")),
            acquired_at=float(data["acquired_at"]),
            agent=str(data.get("agent", "")),
        )
    except (KeyError, ValueError, TypeError):
        return None


def _write_metadata(meta_path: Path, meta: LockMetadata) -> None:
    tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
    tmp.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, meta_path)


def _clear_metadata(meta_path: Path) -> None:
    try:
        meta_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def is_locked(cwd: Optional[Path] = None) -> bool:
    """Return True if the grip lock is currently held by any process.

    This opens the fcntl file and attempts a non-blocking shared lock to
    probe. If the probe fails with EAGAIN/EWOULDBLOCK, some process holds
    an exclusive lock. If the probe succeeds, it is released immediately.
    """
    try:
        fcntl_path, _ = lock_paths(cwd)
    except GitLockError:
        return False
    if not fcntl_path.exists():
        return False
    try:
        with open(fcntl_path, "r+") as fd:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
                return False
            except OSError as e:
                if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return True
                return False
    except OSError:
        return False


def current_holder(cwd: Optional[Path] = None) -> Optional[LockMetadata]:
    """Return the metadata of the current lock holder, or None if free.

    Note that metadata can be stale if a holder crashed between writing
    metadata and releasing flock. Use `is_locked()` as the authoritative
    liveness check; metadata is best-effort human context.
    """
    try:
        _, meta_path = lock_paths(cwd)
    except GitLockError:
        return None
    return _read_metadata(meta_path)


@contextmanager
def grip_git_lock(
    operation: str = "git",
    *,
    reason: str = "",
    timeout: float = 30.0,
    poll_interval: float = 0.2,
    cwd: Optional[Path] = None,
) -> Iterator[LockMetadata]:
    """Acquire the GRIP git lock as a context manager.

    Blocks until the lock is acquired or `timeout` seconds elapse. On
    timeout, raises GitLockTimeout carrying a snapshot of the holder's
    metadata if it can be read.

    Args:
        operation: short label for the git action (e.g. "commit", "add", "push").
        reason: free-form human context ("closeout ritual", "rsi loop commit").
        timeout: max seconds to wait for acquisition.
        poll_interval: how often to retry while queued.
        cwd: optional working directory; defaults to the current process cwd.

    Yields:
        LockMetadata describing this session's hold.
    """
    fcntl_path, meta_path = lock_paths(cwd)
    fcntl_path.parent.mkdir(parents=True, exist_ok=True)
    fcntl_path.touch(exist_ok=True)

    fd = open(fcntl_path, "r+")
    deadline = time.monotonic() + max(0.0, timeout)
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise GitLockError(f"fcntl.flock failed: {e}") from e
                if time.monotonic() >= deadline:
                    raise GitLockTimeout(_read_metadata(meta_path), timeout)
                time.sleep(poll_interval)

        meta = LockMetadata(
            pid=os.getpid(),
            hostname=socket.gethostname(),
            session_id=_session_id(),
            operation=operation,
            reason=reason,
            acquired_at=time.time(),
            agent=_agent_name(),
        )
        _write_metadata(meta_path, meta)
        yield meta
    finally:
        if acquired:
            _clear_metadata(meta_path)
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fd.close()


def _cli_status() -> int:
    holder = current_holder()
    locked = is_locked()
    out = {
        "held": locked,
        "holder": holder.to_dict() if holder else None,
    }
    print(json.dumps(out, indent=2))
    return 0


def _cli_acquire(argv: list[str]) -> int:
    """Test helper: acquire the lock, sleep for N seconds, release."""
    duration = float(argv[0]) if argv else 1.0
    with grip_git_lock(operation="cli-test", reason="manual hold"):
        print(f"acquired; holding for {duration:.1f}s", file=sys.stderr)
        time.sleep(duration)
    print("released", file=sys.stderr)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("status", "info"):
        return _cli_status()
    if args[0] == "hold":
        return _cli_acquire(args[1:])
    print(f"usage: python3 -m lib.git_lock [status|hold <seconds>]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
