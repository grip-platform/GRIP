#!/usr/bin/env python3
"""List all active GRIP sessions with rich metadata for pair-mode discovery.

Scans ALL projects under ~/.claude/projects/ for active session lock files,
validates process health, checks socket availability, and cross-references
grip/index.json for instance metadata.

Output: JSON array to stdout, errors to stderr.
Exit 0 always (empty array if no sessions).
"""

import json
import os
import sys
import hashlib
from pathlib import Path
from datetime import datetime, timezone

from _compat import windows_pid_alive


CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"


def is_process_alive(pid: int) -> bool:
    """Check if a process is running."""
    if sys.platform == "win32":
        # os.kill is no probe on Windows, and a dead answer deletes the lock (GRIP#8817).
        return windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def compute_uptime_minutes(started_at: str) -> int:
    """Compute uptime in minutes from ISO timestamp."""
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - started
        return max(0, int(delta.total_seconds() / 60))
    except (ValueError, TypeError):
        return 0


def format_uptime(minutes: int) -> str:
    """Format minutes into human-readable uptime."""
    if minutes >= 60:
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins}m"
    return f"{minutes}m"


def calculate_tcp_port(session_id: str) -> int:
    """Calculate TCP port from session ID (matches grip-pair MCP server)."""
    h = hashlib.sha256(session_id.encode()).hexdigest()
    return 19000 + (int(h[:8], 16) % 1000)


def read_index_metadata(grip_dir: Path) -> dict:
    """Read latest instance metadata from grip/index.json."""
    index_path = grip_dir / "index.json"
    result = {}
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


def scan_project(project_dir: Path) -> list:
    """Scan a single project directory for active sessions."""
    sessions_dir = project_dir / "grip" / "active-sessions"
    if not sessions_dir.is_dir():
        return []

    grip_dir = project_dir / "grip"
    index_meta = read_index_metadata(grip_dir)
    results = []

    for lock_file in sessions_dir.glob("session-*.lock"):
        try:
            with open(lock_file, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        pid = data.get("pid", 0)
        session_id = data.get("session_id", "")

        if not pid or not session_id:
            continue

        alive = is_process_alive(pid)

        # Auto-clean stale locks (dead PID)
        if not alive:
            try:
                lock_file.unlink()
            except OSError:
                pass
            continue

        started_at = data.get("started_at", "")
        uptime_min = compute_uptime_minutes(started_at)
        from _compat import grip_socket_path
        socket_path = grip_socket_path(session_id)
        monitoring_enabled = os.path.exists(socket_path)
        tcp_port = calculate_tcp_port(session_id)

        # Build enriched session object
        # Prefer lock file metadata, fall back to index
        project_path = data.get("project_path", str(project_dir))
        session = {
            "session_id": session_id,
            "pid": pid,
            "branch": data.get("branch", "unknown"),
            "label": data.get("label", ""),
            "project_path": project_path,
            "project_name": data.get("project_name", Path(project_path).name),
            "started_at": started_at,
            "uptime_minutes": uptime_min,
            "uptime_display": format_uptime(uptime_min),
            "generation": data.get("generation", index_meta.get("generation", 0)),
            "instance_id": data.get("instance_id", index_meta.get("instance_id", "")),
            "achievement": data.get("achievement", index_meta.get("achievement", "")),
            "message_count": data.get("message_count", index_meta.get("message_count", 0)),
            "alive": alive,
            "monitoring_enabled": monitoring_enabled,
            "socket_path": socket_path if monitoring_enabled else None,
            "tcp_port": tcp_port,
        }

        results.append(session)

    return results


def scan_all_sessions() -> list:
    """Scan all projects for active sessions. Auto-cleans stale locks.

    Returns list of session dicts sorted by uptime descending.
    Importable by other modules (e.g. concurrent-session-detector.py).
    """
    all_sessions = []

    if not PROJECTS_DIR.is_dir():
        return []

    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        try:
            sessions = scan_project(project_dir)
            all_sessions.extend(sessions)
        except Exception as e:
            print(f"Error scanning {project_dir.name}: {e}", file=sys.stderr)

    all_sessions.sort(key=lambda s: s.get("uptime_minutes", 0), reverse=True)
    return all_sessions


def main():
    """Scan all projects and output enriched session list as JSON."""
    all_sessions = scan_all_sessions()
    print(json.dumps(all_sessions, indent=2))


if __name__ == "__main__":
    main()
