"""Atomic file I/O helpers for concurrent session safety.

All JSONL write operations in the copilot system must use these helpers
to prevent data corruption when multiple GRIP sessions run in parallel.

The tempfile + os.replace() pattern ensures:
- No partial writes on crash (atomic rename)
- No interleaving with concurrent read-modify-write operations

Concurrent appends and read-modify-write cycles run under an exclusive
FileLock (lib/file_lock.py). FileLock takes flock from lib/_compat.py: the
POSIX module on Linux and macOS, and a LockFileEx lock on Windows, so the
same lock holds on every platform (GRIP#8759).

Platform Support:
- Linux, macOS and Windows: atomic writes via tempfile+rename, and an
  exclusive lock around concurrent writers
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Dict, List

# Import canonical locking — adds lib/ to path for sibling import
import sys as _sys
_lib_dir = str(Path(__file__).resolve().parent.parent)
if _lib_dir not in _sys.path:
    _sys.path.insert(0, _lib_dir)
from file_lock import FileLock

logger = logging.getLogger(__name__)


def atomic_write_jsonl(path: Path, entries: List[Dict]) -> None:
    """Atomically write a list of dicts as JSONL to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(e) + "\n" for e in entries)

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_append_jsonl(path: Path, entry: dict) -> None:
    """Atomically append a single dict entry to a JSONL file.

    Holds an exclusive FileLock on the file itself (flock through
    lib/_compat.py, on every platform) to prevent data loss during
    concurrent appends. This is more efficient than read-modify-write for
    high-frequency operations like mark_delivered in reminders.

    The lock is advisory but sufficient for GRIP's single-machine use case.
    For distributed systems, consider a lock file or database.

    Args:
        path: Path to JSONL file
        entry: Dict to append as JSON line
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Serialize entry first (fail fast on bad data)
    line = json.dumps(entry) + "\n"

    # Open in append mode with exclusive lock (timeout-safe)
    with FileLock(path, timeout=5):
        with open(path, "a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())  # Ensure data hits disk


def locked_read_modify_write(
    path: Path,
    entries: List[Dict],
) -> None:
    """Atomically rewrite a JSONL file with file locking.

    Use this for operations that need to modify existing entries
    (like mark_delivered). Holds an exclusive lock during the
    entire read-modify-write cycle.

    Uses a separate .lock file to coordinate access, on every platform.

    Args:
        path: Path to JSONL file
        entries: Complete list of entries to write
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(path, timeout=10, sidecar=True):
        _atomic_write_content(path, entries)


def _atomic_write_content(path: Path, entries: List[Dict]) -> None:
    """Internal helper to write content atomically."""
    content = "".join(json.dumps(e) + "\n" for e in entries)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_jsonl(path: Path) -> List[Dict]:
    """Read a JSONL file, skipping malformed lines.

    Args:
        path: Path to JSONL file

    Returns:
        List of parsed dict entries. Malformed lines are logged and skipped.
    """
    if not path.exists():
        return []

    entries = []
    lines = path.read_text().strip().split("\n")
    for line_num, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as e:
            logger.debug("Skipping malformed line %d in %s: %s", line_num, path.name, e)
            continue
    return entries
