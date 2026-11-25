#!/usr/bin/env python3
"""
Session Coordinator — cross-session coordination for concurrent worktree work.

Reads branch lock state, detects file conflicts before commit, and provides
advisory mode detection when multiple sessions overlap on the same files.

Usage:
    python3 lib/session_coordinator.py active          # List active sessions
    python3 lib/session_coordinator.py cleanup         # Remove stale locks
    python3 lib/session_coordinator.py conflicts FILE  # Check file conflicts
    python3 lib/session_coordinator.py advisory FILE   # Check advisory mode
"""

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class SessionInfo:
    """Represents a single active session from a branch lock file."""

    branch: str
    session_id: str
    pid: int
    timestamp: float
    worktree_path: str
    lock_file: str
    is_alive: bool = True


@dataclass
class FileConflict:
    """A file that is modified in multiple active sessions."""

    file_path: str
    session_branches: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Lock directory resolution
# ---------------------------------------------------------------------------


def _get_lock_dir(repo_root: Optional[str] = None) -> Path:
    """Return the branch-locks directory path."""
    if repo_root is None:
        repo_root = str(Path(__file__).resolve().parent.parent)
    return Path(repo_root) / "state" / "branch-locks"


# ---------------------------------------------------------------------------
# PID liveness check
# ---------------------------------------------------------------------------


def _is_pid_alive(pid: int) -> bool:
    """Check whether a process with the given PID is still running.

    Uses os.kill with signal 0 (no signal sent, just checks existence).
    Returns False for invalid PIDs (<=0).

    Windows is different (GRIP#8835). There ``os.kill(pid, 0)`` sends Ctrl+C to a
    console process group instead of checking that a process exists, and a lock's
    PID is a Git Bash PID, so ``_git_bash_pid_alive`` looks the owner up in Git's
    ``ps -W`` listing instead.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _git_bash_pid_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we lack permission — still alive
        return True
    except OSError:
        return False


def _git_bash_pid_alive(pid: int) -> bool:
    """A lock owner on Windows is alive while Git's ``ps -W`` lists it.

    The listing is read fresh for every lock, so an owner that took its lock after
    an earlier read never looks dead. It is trusted only when this process's parent
    has a Git Bash PID in it, which shows that it lists the Git Bash that wrote the
    lock. A listing from another Cygwin-family runtime, or no listing at all, cannot
    judge a Git Bash PID, so the owner counts as alive and the lock is kept. A lock
    that records its owner's Windows PID counts too.
    """
    processes = _windows_processes()
    table = processes.git_bash_pid_table() if processes else None
    if table is None or not processes.started_by_git_bash(table, os.getppid()):
        return True
    return processes.pid_listed(table, pid)


def _windows_processes():
    """``lib/windows_processes.py``, or None when it cannot be imported."""
    try:
        import windows_processes
    except ImportError:
        return None
    return windows_processes


# ---------------------------------------------------------------------------
# Lock file parsing
# ---------------------------------------------------------------------------


def _parse_lock_file(lock_path: Path) -> Optional[SessionInfo]:
    """Parse a single .lock file into a SessionInfo, or None on failure."""
    try:
        content = lock_path.read_text(encoding="utf-8")
    except OSError:
        return None

    fields: Dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()

    session_id = fields.get("SESSION_ID", "unknown")
    pid_str = fields.get("PID", "0")
    timestamp_str = fields.get("TIMESTAMP", "0")
    worktree_path = fields.get("PATH", "")

    try:
        pid = int(pid_str)
    except ValueError:
        pid = 0

    try:
        timestamp = float(timestamp_str)
    except ValueError:
        timestamp = 0.0

    # Derive branch name from lock filename (reverse of _lock_file_for)
    # Lock files are named {branch_with_slashes_as_underscores}.lock
    branch = lock_path.stem.replace("_", "/")

    alive = _is_pid_alive(pid)

    return SessionInfo(
        branch=branch,
        session_id=session_id,
        pid=pid,
        timestamp=timestamp,
        worktree_path=worktree_path,
        lock_file=str(lock_path),
        is_alive=alive,
    )


# ---------------------------------------------------------------------------
# A. Health monitor lock integration
# ---------------------------------------------------------------------------


def get_active_sessions(repo_root: Optional[str] = None) -> List[SessionInfo]:
    """Read all branch lock files and return sessions whose PIDs are alive."""
    lock_dir = _get_lock_dir(repo_root)
    if not lock_dir.is_dir():
        return []

    sessions: List[SessionInfo] = []
    for lock_path in sorted(lock_dir.glob("*.lock")):
        info = _parse_lock_file(lock_path)
        if info is not None and info.is_alive:
            sessions.append(info)

    return sessions


def get_all_sessions(repo_root: Optional[str] = None) -> List[SessionInfo]:
    """Read all branch lock files, including stale ones."""
    lock_dir = _get_lock_dir(repo_root)
    if not lock_dir.is_dir():
        return []

    sessions: List[SessionInfo] = []
    for lock_path in sorted(lock_dir.glob("*.lock")):
        info = _parse_lock_file(lock_path)
        if info is not None:
            sessions.append(info)

    return sessions


def cleanup_stale_locks(
    repo_root: Optional[str] = None, dry_run: bool = False
) -> List[str]:
    """Remove lock files whose PIDs are no longer running.

    Returns list of lock file paths that were (or would be) removed.
    """
    lock_dir = _get_lock_dir(repo_root)
    if not lock_dir.is_dir():
        return []

    removed: List[str] = []
    for lock_path in sorted(lock_dir.glob("*.lock")):
        info = _parse_lock_file(lock_path)
        if info is None:
            continue
        if not info.is_alive:
            removed.append(str(lock_path))
            if not dry_run:
                try:
                    lock_path.unlink()
                except OSError:
                    pass

    return removed


# ---------------------------------------------------------------------------
# B. Cross-session file conflict detection
# ---------------------------------------------------------------------------


def _get_modified_files_in_worktree(worktree_path: str) -> Set[str]:
    """Return the set of modified/staged file paths in a worktree.

    Uses git diff (both staged and unstaged) to find all changed files
    relative to HEAD.
    """
    if not os.path.isdir(worktree_path):
        return set()

    files: Set[str] = set()
    for diff_args in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--name-only", "--cached"],
    ):
        try:
            result = subprocess.run(
                diff_args,
                capture_output=True,
                text=True,
                cwd=worktree_path,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    stripped = line.strip()
                    if stripped:
                        files.add(stripped)
        except (subprocess.TimeoutExpired, OSError):
            continue

    return files


def check_file_conflicts(
    files_to_commit: List[str],
    repo_root: Optional[str] = None,
    exclude_session_id: Optional[str] = None,
) -> List[FileConflict]:
    """Check whether any files overlap with other active sessions' changes.

    Designed as a pre-commit gate: pass in the list of files you intend to
    commit, and this returns any that are also modified in other active
    worktrees.

    Args:
        files_to_commit: Relative file paths to check.
        repo_root: Repository root for lock dir resolution.
        exclude_session_id: Skip this session's own worktree (avoids self-conflict).

    Returns:
        List of FileConflict objects for files modified in multiple sessions.
    """
    if not files_to_commit:
        return []

    target_set = set(files_to_commit)
    sessions = get_active_sessions(repo_root)
    conflicts: Dict[str, List[str]] = {}

    for session in sessions:
        if exclude_session_id and session.session_id == exclude_session_id:
            continue

        other_files = _get_modified_files_in_worktree(session.worktree_path)
        overlap = target_set & other_files
        for f in overlap:
            conflicts.setdefault(f, []).append(session.branch)

    return [
        FileConflict(file_path=fp, session_branches=branches)
        for fp, branches in sorted(conflicts.items())
    ]


# ---------------------------------------------------------------------------
# D. Advisory mode detection
# ---------------------------------------------------------------------------


def is_advisory_mode(
    target_files: List[str],
    repo_root: Optional[str] = None,
    exclude_session_id: Optional[str] = None,
) -> bool:
    """Return True if another active session is modifying overlapping files.

    A secondary session should enter advisory mode (read-only suggestions,
    no direct edits) when this returns True.

    Args:
        target_files: Files the current session intends to work on.
        repo_root: Repository root for lock dir resolution.
        exclude_session_id: This session's ID to exclude from checks.
    """
    conflicts = check_file_conflicts(
        target_files,
        repo_root=repo_root,
        exclude_session_id=exclude_session_id,
    )
    return len(conflicts) > 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_sessions(sessions: List[SessionInfo]) -> None:
    """Print session info to stdout."""
    if not sessions:
        print("No active sessions.")
        return
    for s in sessions:
        status = "alive" if s.is_alive else "dead"
        print(
            f"  [{status}] branch={s.branch}  pid={s.pid}  "
            f"session={s.session_id}  path={s.worktree_path}"
        )


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print("Usage: session_coordinator.py {active|cleanup|conflicts|advisory} [args...]")
        return 1

    command = argv[0]

    if command == "active":
        sessions = get_active_sessions()
        _print_sessions(sessions)
        return 0

    elif command == "cleanup":
        dry_run = "--dry-run" in argv
        removed = cleanup_stale_locks(dry_run=dry_run)
        action = "Would remove" if dry_run else "Removed"
        print(f"{action} {len(removed)} stale lock(s)")
        for path in removed:
            print(f"  {path}")
        return 0

    elif command == "conflicts":
        files = argv[1:]
        if not files:
            print("Usage: session_coordinator.py conflicts FILE [FILE...]")
            return 1
        conflicts = check_file_conflicts(files)
        if not conflicts:
            print("No file conflicts detected.")
            return 0
        for c in conflicts:
            print(f"  CONFLICT: {c.file_path} (also modified in: {', '.join(c.session_branches)})")
        return 2

    elif command == "advisory":
        files = argv[1:]
        if not files:
            print("Usage: session_coordinator.py advisory FILE [FILE...]")
            return 1
        if is_advisory_mode(files):
            print("ADVISORY MODE: overlapping files detected in another active session.")
            return 2
        print("No overlapping sessions. Safe to proceed.")
        return 0

    else:
        print(f"Unknown command: {command}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
