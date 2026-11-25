#!/usr/bin/env python3
"""
GRIP Session Registry - Concurrency Detection for Parallel Sessions

Manages active Claude Code sessions within a project to enable GRIP branching.
When multiple sessions run simultaneously, each gets assigned a unique branch.

Architecture:
    ~/.claude/projects/{path}/grip/
    ├── active-sessions/
    │   ├── session-{uuid}.lock    # PID + timestamp + branch
    │   └── session-{uuid}.lock
    └── branches/
        ├── main.json              # Branch metadata
        ├── alpha.json
        └── bravo.json

Usage:
    from grip_registry import GRIPRegistry

    registry = GRIPRegistry()
    branch = registry.register_session()  # Returns "main" or "alpha", etc.
    registry.deregister_session()

CLI:
    python3 grip_registry.py register   # Register and print branch
    python3 grip_registry.py deregister # Remove session lock
    python3 grip_registry.py status     # List active sessions

Version: 1.0.0
Date: 2025-12-20
"""

import json
import os
import subprocess
import sys
from file_lock import FileLock
from _compat import windows_pid_alive
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from json_utils import safe_load_json

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"

# NATO phonetic alphabet for branch names
BRANCH_NAMES = [
    "main",     # Default for single session
    "alpha",    # First parallel session
    "bravo",    # Second parallel session
    "charlie",  # Third parallel session
    "delta",    # Fourth parallel session
    "echo",     # Fifth parallel session
    "foxtrot",  # Sixth parallel session
    "golf",     # Seventh parallel session
    "hotel",    # Eighth parallel session
    "india",    # Ninth parallel session
    "juliet",   # Tenth parallel session
]

# Stale lock threshold (1 hour in seconds)
STALE_LOCK_THRESHOLD = 3600


def _resolve_claude_pid() -> int:
    """Find Claude Code's PID by walking up the process tree.

    Process chain: Claude Code -> bash shell -> python3 (this script)
    So grandparent PID = Claude Code PID.
    """
    claude_pid = os.environ.get("CLAUDE_PID")
    if claude_pid:
        return int(claude_pid)
    try:
        ppid = os.getppid()
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(ppid)],
            capture_output=True, text=True, timeout=2,
        )
        return int(result.stdout.strip())
    except Exception:
        return os.getppid()


def _resolve_session_id(claude_pid: int) -> str:
    """Resolve stable session ID from breadcrumb file or env var."""
    env_id = os.environ.get("CLAUDE_SESSION_ID")
    if env_id:
        return env_id
    from _compat import grip_sessions_dir
    breadcrumb = Path(grip_sessions_dir()) / f"sid-{claude_pid}"
    if breadcrumb.exists():
        return breadcrumb.read_text().strip()
    return str(claude_pid)

# Import unified path encoding from this module's own folder. This used to put
# ~/.claude/lib first on sys.path, so every later import in the process loaded the
# shared tree's copy, whichever branch it is on, even in a worktree or a test run of
# another checkout; the tests then saw modules from the wrong tree.
_OWN_LIB = str(Path(__file__).resolve().parent)
if _OWN_LIB not in sys.path:
    sys.path.insert(0, _OWN_LIB)
try:
    from path_encoding import encode_project_path
except ImportError:
    # Fallback if path_encoding not available
    def encode_project_path(path: Path) -> str:
        return str(path).replace("/", "-").replace(".", "-")


@dataclass
class SessionInfo:
    """Information about an active session."""
    session_id: str
    branch: str
    pid: int
    started_at: str
    lock_file: str

    def is_alive(self) -> bool:
        """Check if the session process is still running."""
        if sys.platform == "win32":
            # os.kill is no probe on Windows (GRIP#8817); psutil is, through _compat.
            return windows_pid_alive(self.pid)
        try:
            os.kill(self.pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def is_stale(self) -> bool:
        """Check if the lock is stale (process dead or too old)."""
        if not self.is_alive():
            return True

        try:
            started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - started).total_seconds()
            return age > STALE_LOCK_THRESHOLD
        except (ValueError, TypeError):
            return True


class GRIPRegistry:
    """Manage concurrent session registration for GRIP branching."""

    def __init__(self, project_path: Optional[Path] = None):
        self.project_path = project_path or Path.cwd()
        self.encoded_path = encode_project_path(self.project_path)
        self.grip_dir = PROJECTS_DIR / self.encoded_path / "grip"
        self.sessions_dir = self.grip_dir / "active-sessions"
        self.branches_dir = self.grip_dir / "branches"
        self.lock_file_path = self.grip_dir / ".registry.lock"

        self.pid = _resolve_claude_pid()
        self.session_id = _resolve_session_id(self.pid)
        self.current_branch: Optional[str] = None

        # Ensure directories exist
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.branches_dir.mkdir(parents=True, exist_ok=True)

    def _acquire_lock(self):
        """Acquire exclusive lock for registry operations."""
        self._file_lock = FileLock(self.lock_file_path, timeout=10)
        self._file_lock.__enter__()

    def _release_lock(self):
        """Release registry lock."""
        if hasattr(self, "_file_lock") and self._file_lock:
            self._file_lock.__exit__(None, None, None)
            self._file_lock = None

    def _get_session_lock_path(self) -> Path:
        """Get lock file path for current session."""
        return self.sessions_dir / f"session-{self.session_id}.lock"

    def _read_session_lock(self, lock_path: Path) -> Optional[SessionInfo]:
        """Read session info from lock file."""
        data = safe_load_json(lock_path)
        if data is None:
            return None
        try:
            return SessionInfo(
                session_id=data["session_id"],
                branch=data["branch"],
                pid=data["pid"],
                started_at=data["started_at"],
                lock_file=str(lock_path)
            )
        except KeyError:
            return None

    def _read_index_metadata(self) -> Dict[str, Any]:
        """Read latest instance metadata from grip/index.json."""
        index_path = self.grip_dir / "index.json"
        result: Dict[str, Any] = {}
        try:
            if index_path.exists():
                with open(index_path, "r") as f:
                    index = json.load(f)
                lineage = index.get("lineage", [])
                if lineage:
                    latest = lineage[-1]
                    result["instance_id"] = latest.get("instance_id", "")
                    result["generation"] = latest.get("generation", 0)
                    result["achievement"] = latest.get("achievement", "")
                    result["message_count"] = latest.get("message_count", 0)
        except (json.JSONDecodeError, KeyError, OSError):
            pass
        return result

    def _write_session_lock(self, branch: str):
        """Write session lock file with enriched metadata."""
        lock_path = self._get_session_lock_path()
        data = {
            "session_id": self.session_id,
            "branch": branch,
            "pid": self.pid,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "project_path": str(self.project_path),
            "project_name": self.project_path.name,
            "cwd": os.getcwd(),
            "label": os.environ.get("GRIP_SESSION_LABEL", ""),
        }

        # Enrich with index metadata (graceful fallback)
        index_meta = self._read_index_metadata()
        if index_meta:
            data.update(index_meta)

        with open(lock_path, "w") as f:
            json.dump(data, f, indent=2)

    def update_label(self, session_id: str, label: str) -> bool:
        """Update the label on an existing session lock file."""
        lock_path = self.sessions_dir / f"session-{session_id}.lock"
        if not lock_path.exists():
            return False
        try:
            with open(lock_path, "r") as f:
                data = json.load(f)
            data["label"] = label
            with open(lock_path, "w") as f:
                json.dump(data, f, indent=2)
            return True
        except (json.JSONDecodeError, OSError):
            return False

    def _cleanup_stale_locks(self) -> List[str]:
        """Remove stale lock files and return list of removed branches."""
        removed = []
        for lock_file in self.sessions_dir.glob("session-*.lock"):
            session = self._read_session_lock(lock_file)
            if session and session.is_stale():
                try:
                    lock_file.unlink()
                    removed.append(session.branch)
                except OSError:
                    pass
        return removed

    def _get_active_branches(self) -> List[str]:
        """Get list of currently active branches."""
        branches = []
        for lock_file in self.sessions_dir.glob("session-*.lock"):
            session = self._read_session_lock(lock_file)
            if session and not session.is_stale():
                branches.append(session.branch)
        return branches

    def _next_branch_name(self, active_branches: List[str]) -> str:
        """Get next available branch name."""
        for name in BRANCH_NAMES:
            if name not in active_branches:
                return name

        # Fallback if all NATO names used (unlikely)
        counter = len(BRANCH_NAMES)
        while f"branch-{counter}" in active_branches:
            counter += 1
        return f"branch-{counter}"

    def _ensure_branch_metadata(self, branch: str, fork_point: Optional[str] = None):
        """Create or update branch metadata file."""
        branch_file = self.branches_dir / f"{branch}.json"

        if branch_file.exists():
            return  # Already exists

        metadata = {
            "name": branch,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "fork_point": fork_point,
            "is_main": branch == "main"
        }

        with open(branch_file, "w") as f:
            json.dump(metadata, f, indent=2)

    def register_session(self) -> str:
        """
        Register this session and return assigned branch.

        Returns:
            Branch name: "main" for single session, "alpha/bravo/..." for parallel
        """
        try:
            self._acquire_lock()

            # Clean up stale locks first
            self._cleanup_stale_locks()

            # Check for existing registration (idempotent)
            existing_lock = self._get_session_lock_path()
            if existing_lock.exists():
                session = self._read_session_lock(existing_lock)
                if session and session.pid == self.pid:
                    self.current_branch = session.branch
                    return session.branch

            # Get currently active branches
            active_branches = self._get_active_branches()

            # Assign branch
            if not active_branches:
                # First/only session - use main
                branch = "main"
                fork_point = None
            elif "main" not in active_branches:
                # Main available - take it
                branch = "main"
                fork_point = None
            else:
                # Parallel session - get next available
                branch = self._next_branch_name(active_branches)
                # Fork from main's latest (will be resolved by serializer)
                fork_point = "main"

            # Write session lock
            self._write_session_lock(branch)

            # Ensure branch metadata exists
            self._ensure_branch_metadata(branch, fork_point)

            self.current_branch = branch
            return branch

        finally:
            self._release_lock()

    def deregister_session(self) -> bool:
        """
        Remove this session from the registry.

        Returns:
            True if successfully deregistered, False otherwise
        """
        try:
            self._acquire_lock()

            lock_path = self._get_session_lock_path()
            if lock_path.exists():
                lock_path.unlink()
                self.current_branch = None
                return True
            return False

        finally:
            self._release_lock()

    def get_current_branch(self) -> Optional[str]:
        """Get the branch assigned to current session."""
        if self.current_branch:
            return self.current_branch

        # Check lock file
        lock_path = self._get_session_lock_path()
        if lock_path.exists():
            session = self._read_session_lock(lock_path)
            if session:
                self.current_branch = session.branch
                return session.branch

        return None

    def list_active_sessions(self) -> List[SessionInfo]:
        """List all active sessions in this project."""
        self._cleanup_stale_locks()

        sessions = []
        for lock_file in self.sessions_dir.glob("session-*.lock"):
            session = self._read_session_lock(lock_file)
            if session and not session.is_stale():
                sessions.append(session)

        return sorted(sessions, key=lambda s: s.started_at)

    def get_branch_info(self, branch: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a branch."""
        branch_file = self.branches_dir / f"{branch}.json"
        return safe_load_json(branch_file)

    def list_branches(self) -> List[Dict[str, Any]]:
        """List all branches with their metadata."""
        branches = []
        for branch_file in self.branches_dir.glob("*.json"):
            data = safe_load_json(branch_file)
            if data is not None:
                branches.append(data)
        return sorted(branches, key=lambda b: b.get("created_at", ""))

    def count_siblings(self, branch: str) -> int:
        """Count sibling branches (other branches at same fork point)."""
        branch_info = self.get_branch_info(branch)
        if not branch_info:
            return 0

        fork_point = branch_info.get("fork_point")
        if not fork_point:
            return 0

        count = 0
        for other_branch in self.list_branches():
            if other_branch.get("name") != branch:
                if other_branch.get("fork_point") == fork_point:
                    count += 1

        return count


    def get_session_control_info(self, session_id: str) -> dict | None:
        """Get control server info for a session.

        Returns dict with socket_path, tcp_port, monitoring_enabled or None if session not found.
        """
        import hashlib

        lock_path = self.sessions_dir / f"session-{session_id}.lock"
        session = self._read_session_lock(lock_path)
        if not session or session.is_stale():
            return None

        from _compat import grip_socket_path
        socket_path = grip_socket_path(session_id)
        tcp_port = 19000 + (int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16) % 1000)

        # Check if monitoring enabled via lock file metadata or env
        monitoring_enabled = os.path.exists(socket_path)

        return {
            "session_id": session_id,
            "branch": session.branch,
            "pid": session.pid,
            "socket_path": socket_path,
            "tcp_port": tcp_port,
            "monitoring_enabled": monitoring_enabled,
            "started_at": session.started_at,
        }

    def list_monitorable_sessions(self) -> list[dict]:
        """List all sessions that have monitoring enabled (control server running)."""
        sessions = self.list_active_sessions()
        monitorable = []
        for session in sessions:
            info = self.get_session_control_info(session.session_id)
            if info and info["monitoring_enabled"]:
                monitorable.append(info)
        return monitorable


def main():
    """CLI interface for GRIP registry."""
    import argparse

    parser = argparse.ArgumentParser(description="GRIP Session Registry")
    parser.add_argument(
        "command",
        choices=["register", "deregister", "status", "branch", "siblings", "monitorable", "update-label"],
        help="Command to execute"
    )
    parser.add_argument("--label", "-l", help="Session label (for update-label command)")
    parser.add_argument("--session-id", "-s", help="Target session ID (for update-label command)")
    parser.add_argument("--project", "-p", help="Project path (default: current directory)")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    project_path = Path(args.project) if args.project else None
    registry = GRIPRegistry(project_path=project_path)

    if args.command == "register":
        branch = registry.register_session()
        if args.json:
            print(json.dumps({"branch": branch, "session_id": registry.session_id}))
        else:
            print(branch)

    elif args.command == "deregister":
        success = registry.deregister_session()
        if args.json:
            print(json.dumps({"success": success}))
        else:
            print("deregistered" if success else "not_found")

    elif args.command == "status":
        sessions = registry.list_active_sessions()
        if args.json:
            print(json.dumps([asdict(s) for s in sessions], indent=2))
        else:
            if not sessions:
                print("No active sessions")
            else:
                print(f"Active sessions ({len(sessions)}):")
                for s in sessions:
                    alive = "ALIVE" if s.is_alive() else "DEAD"
                    print(f"  {s.branch}: PID {s.pid} ({alive}) - {s.started_at}")

    elif args.command == "branch":
        branch = registry.get_current_branch()
        if args.json:
            print(json.dumps({"branch": branch}))
        else:
            print(branch or "unregistered")

    elif args.command == "siblings":
        branch = registry.get_current_branch()
        if branch:
            count = registry.count_siblings(branch)
            if args.json:
                print(json.dumps({"branch": branch, "siblings": count}))
            else:
                print(f"{count} sibling branches")
        else:
            if args.json:
                print(json.dumps({"error": "not registered"}))
            else:
                print("Session not registered")
            sys.exit(1)

    elif args.command == "monitorable":
        monitorable = registry.list_monitorable_sessions()
        if args.json:
            print(json.dumps(monitorable, indent=2))
        else:
            if not monitorable:
                print("No monitorable sessions")
            else:
                print(f"Monitorable sessions ({len(monitorable)}):")
                for s in monitorable:
                    print(f"  {s['session_id'][:8]}... branch={s['branch']} port={s['tcp_port']}")

    elif args.command == "update-label":
        sid = args.session_id or registry.session_id
        label = args.label or ""
        success = registry.update_label(sid, label)
        if args.json:
            print(json.dumps({"success": success, "session_id": sid, "label": label}))
        else:
            print(f"label_updated:{label}" if success else "not_found")


if __name__ == "__main__":
    main()
