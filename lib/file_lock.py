"""Canonical file locking for GRIP.

Single implementation replacing 10+ independent locking patterns.
Uses LOCK_NB + polling for timeout safety (never blocks forever).

Usage:
    from file_lock import FileLock, LockTimeoutError

    # Exclusive lock (default)
    with FileLock("/path/to/file"):
        # ... write operations ...

    # Shared lock for readers
    with FileLock("/path/to/file", shared=True):
        # ... read operations ...

    # Sidecar lock (locks <path>.lock instead of data file)
    with FileLock("/path/to/data.json", sidecar=True):
        # ... overwrite data file safely ...

    # Convenience functions (backwards-compatible)
    locked_append("/path/to/file.jsonl", '{"key": "value"}')
    locked_write("/path/to/file.md", "full content")
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from _compat import fcntl


class LockTimeoutError(Exception):
    """Raised when file lock acquisition times out."""


class FileLock:
    """Advisory file lock with mandatory timeout.

    Uses LOCK_NB + polling (10ms interval) for timeout safety.
    This avoids the blocking flock() that caused Gen 416 deadlocks.

    Args:
        path: File to lock. If sidecar=True, locks <path>.lock instead.
        timeout: Seconds to wait before raising LockTimeoutError.
        sidecar: Lock a separate .lock file alongside the data file.
                 Use for overwrite operations where the data file is replaced.
        shared: Use LOCK_SH instead of LOCK_EX (multiple readers allowed).
    """

    def __init__(
        self,
        path: str | Path,
        timeout: float = 5,
        sidecar: bool = False,
        shared: bool = False,
    ):
        self._path = Path(path)
        self._timeout = timeout
        self._sidecar = sidecar
        self._shared = shared
        self._fd = None

    @property
    def lock_path(self) -> Path:
        """The actual file being locked, resolved so the path names the file.

        Resolving does NOT change which file is locked. A symlinked alias
        (``~/.claude`` -> ``~/.grip``) already locked the same inode, because
        the OS follows the link at ``open()`` -- two spellings, one lock file,
        and they contend normally. What resolving fixes is the REPORT: the
        unresolved form made one lock file look like two in a debugger and in
        the LockTimeoutError message, which is actively misleading when the
        question being asked is "why did these two not contend?".

        ``locked_append`` below has always resolved; this makes the class
        agree with its own convenience functions.
        """
        base = self._path.resolve()
        if self._sidecar:
            return base.with_suffix(base.suffix + ".lock")
        return base

    def __enter__(self) -> "FileLock":
        lock_path = self.lock_path
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        mode = "r" if (self._shared and lock_path.exists()) else "a"
        self._fd = open(lock_path, mode)

        lock_op = fcntl.LOCK_SH if self._shared else fcntl.LOCK_EX
        start = time.monotonic()

        while True:
            try:
                fcntl.flock(self._fd.fileno(), lock_op | fcntl.LOCK_NB)
                return self  # Lock acquired
            except (BlockingIOError, OSError):
                elapsed = time.monotonic() - start
                if elapsed >= self._timeout:
                    self._fd.close()
                    self._fd = None
                    raise LockTimeoutError(
                        f"Could not acquire {'shared' if self._shared else 'exclusive'} "
                        f"lock on {lock_path} within {self._timeout}s"
                    )
                time.sleep(0.01)  # 10ms polling interval

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd:
            try:
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self._fd.close()
            self._fd = None
        return False  # Don't suppress exceptions


# ── Convenience functions (backwards-compatible API) ──────────────


def locked_append(filepath: str | Path, line: str, timeout: float = 5) -> None:
    """Append a line to a file with exclusive file lock.

    Drop-in replacement for the original locked_append.py.

    Args:
        filepath: Path to file to append to.
        line: Line content to append (newline added if missing).
        timeout: Seconds to wait for lock before raising LockTimeoutError.
    """
    filepath = Path(filepath).resolve()
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(filepath, timeout=timeout):
        with open(filepath, "a") as f:
            f.write(line if line.endswith("\n") else line + "\n")
            f.flush()
            os.fsync(f.fileno())


def locked_write(path: str | Path, content: str, timeout: float = 10) -> None:
    """Overwrite a file atomically while holding an exclusive lock.

    Uses sidecar lock + tempfile-rename for crash safety.

    Args:
        path: Target file path.
        content: Full replacement content.
        timeout: Seconds to wait for lock.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(path, timeout=timeout, sidecar=True):
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        try:
            os.write(fd, content.encode("utf-8"))
            os.fsync(fd)
            os.close(fd)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
