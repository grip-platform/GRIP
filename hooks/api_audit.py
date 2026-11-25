#!/usr/bin/env python3
"""
API Audit Logger for GRIP
=========================

Logs all Claude Code tool calls to ~/.claude/logs/api-audit.jsonl
for REDR compliance and security traceability.

Features:
- Automatic log rotation at configurable size threshold
- Archived logs stored in ~/.claude/logs/archive/
- Timestamps in filenames for audit trail

Format:
{
  "ts": "2026-02-02T10:30:00Z",
  "session_id": "abc123",
  "user": "username",
  "service": "claude-code",
  "tool": "Bash",
  "action_summary": "git status",
  "mode": "code",
  "project": "~/.claude",
  "decision": "allow"
}

Version: 1.1.0
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Import audit chain for tamper-evident signing
sys.path.insert(0, str(Path.home() / ".claude" / "lib"))
try:
    from audit_chain import sign_and_append
    _CHAIN_AVAILABLE = True
except ImportError:
    _CHAIN_AVAILABLE = False

# Log file locations
AUDIT_LOG = Path.home() / ".claude" / "logs" / "api-audit.jsonl"
ARCHIVE_DIR = Path.home() / ".claude" / "logs" / "archive"

# Rotation settings
MAX_LOG_SIZE_MB = int(os.environ.get("AUDIT_LOG_MAX_MB", 5))  # Default 5MB
MAX_LOG_SIZE_BYTES = MAX_LOG_SIZE_MB * 1024 * 1024

# Current mode (from environment or default)
def get_current_mode() -> str:
    """Get current GRIP mode from environment or state."""
    mode_env = os.environ.get("GRIP_MODE", "")
    if mode_env:
        return mode_env

    # Try reading from orchestrator state, under state/session/ since April
    # audit task D5; state_file copies a value left at the old root file once.
    try:
        from state_paths import SESSION_ORCHESTRATOR, state_file
        state = json.loads(state_file(SESSION_ORCHESTRATOR).read_text())
        return state.get("mode", "code")
    except Exception:
        return "code"


def get_user() -> str:
    """Get current user from environment or identity file."""
    # Try environment first
    user_env = os.environ.get("USER") or os.environ.get("USERNAME", "")
    if user_env:
        return user_env

    # Try identity file
    identity_file = Path.home() / ".claude" / "facts" / "identity.md"
    if identity_file.exists():
        try:
            content = identity_file.read_text()
            # Look for "name:" or "Name:" pattern
            match = re.search(r"[Nn]ame:\s*(.+)", content)
            if match:
                return match.group(1).strip()
        except Exception:
            pass

    return "unknown"


def get_project(transcript_path: str) -> str:
    """Extract project path from transcript path."""
    if not transcript_path:
        return "unknown"

    # Transcript path format: ~/.claude/projects/<encoded-path>/sessions/<id>.jsonl
    # Extract the project portion
    try:
        parts = Path(transcript_path).parts
        if "projects" in parts:
            idx = parts.index("projects")
            if idx + 1 < len(parts):
                # Decode the project path (replace - with /, -- with .)
                encoded = parts[idx + 1]
                # This is approximate - just return the encoded form for now
                return encoded
    except Exception:
        pass

    return "unknown"


def sanitize_bash(command: str) -> str:
    """Sanitize bash command - keep command name, remove sensitive args."""
    if not command:
        return ""

    # Extract first command (before pipes, &&, ||)
    first_cmd = re.split(r'\s*[|&;]\s*', command)[0].strip()

    # Get command name
    parts = first_cmd.split()
    if not parts:
        return ""

    cmd_name = parts[0]

    # For common commands, include first argument (usually a flag or target)
    safe_commands = {"git", "npm", "python3", "node", "rg", "fd", "ls", "cd", "mkdir", "touch"}
    if cmd_name in safe_commands and len(parts) > 1:
        # Include subcommand for git
        if cmd_name == "git" and len(parts) > 1:
            return f"git {parts[1]}"
        # Include first arg if it's a flag
        if parts[1].startswith("-"):
            return f"{cmd_name} {parts[1]}"
        return cmd_name

    return cmd_name


def sanitize_read(file_path: str) -> str:
    """Sanitize file read - return filename only."""
    if not file_path:
        return ""
    return Path(file_path).name


def sanitize_write(file_path: str) -> str:
    """Sanitize file write - return filename only."""
    if not file_path:
        return ""
    return Path(file_path).name


def sanitize_grep(pattern: str, path: str) -> str:
    """Sanitize grep - indicate pattern search without exposing pattern."""
    target = Path(path).name if path else "cwd"
    return f"pattern search in {target}"


def sanitize_glob(pattern: str) -> str:
    """Sanitize glob - return pattern type."""
    if not pattern:
        return "file search"

    # Categorize pattern
    if "**" in pattern:
        return "recursive file search"
    if "*." in pattern:
        ext = pattern.split("*.")[-1]
        return f"*.{ext} search"
    return "file search"


def sanitize_skill(skill_name: str) -> str:
    """Sanitize skill invocation - return skill name."""
    return skill_name or "unknown skill"


def rotate_if_needed() -> bool:
    """
    Check if log needs rotation and rotate if so.
    Returns True if rotation occurred.
    """
    try:
        if not AUDIT_LOG.exists():
            return False

        file_size = AUDIT_LOG.stat().st_size
        if file_size < MAX_LOG_SIZE_BYTES:
            return False

        # Create archive directory
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

        # Generate archive filename with timestamp
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        archive_name = f"api-audit-{timestamp}.jsonl"
        archive_path = ARCHIVE_DIR / archive_name

        # Move current log to archive
        shutil.move(str(AUDIT_LOG), str(archive_path))

        # Log rotation event to new file
        rotation_entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": "log_rotation",
            "archived_to": str(archive_path),
            "archived_size_mb": round(file_size / (1024 * 1024), 2)
        }
        with open(AUDIT_LOG, "w", encoding="utf-8") as f:
            f.write(json.dumps(rotation_entry) + "\n")

        return True

    except Exception:
        # Never fail on rotation errors
        return False


def sanitize_tool_input(tool_name: str, tool_input: dict) -> str:
    """Sanitize tool input based on tool type."""
    if tool_name == "Bash":
        return sanitize_bash(tool_input.get("command", ""))

    if tool_name == "Read":
        return sanitize_read(tool_input.get("file_path", ""))

    if tool_name == "Write":
        return sanitize_write(tool_input.get("file_path", ""))

    if tool_name == "Edit":
        return sanitize_write(tool_input.get("file_path", ""))

    if tool_name == "Grep":
        return sanitize_grep(
            tool_input.get("pattern", ""),
            tool_input.get("path", "")
        )

    if tool_name == "Glob":
        return sanitize_glob(tool_input.get("pattern", ""))

    if tool_name == "Skill":
        return sanitize_skill(tool_input.get("skill", ""))

    if tool_name == "Task":
        return tool_input.get("description", "subagent task")[:50]

    if tool_name == "WebFetch":
        return "web fetch"

    if tool_name == "WebSearch":
        return "web search"

    # Default: just the tool name
    return tool_name.lower()


def log_tool_call(
    session_id: str,
    tool_name: str,
    tool_input: dict,
    decision: str,
    transcript_path: str = ""
):
    """
    Log a tool call to the audit log.

    This function is designed to NEVER raise exceptions.
    Audit logging should not break the hook pipeline.
    Automatically rotates log when size threshold exceeded.
    """
    try:
        # Ensure log directory exists
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)

        # Check for rotation before writing
        rotate_if_needed()

        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_id": session_id[:8] if session_id else "unknown",
            "user": get_user(),
            "service": "claude-code",
            "tool": tool_name,
            "action_summary": sanitize_tool_input(tool_name, tool_input),
            "mode": get_current_mode(),
            "project": get_project(transcript_path),
            "decision": decision
        }

        if _CHAIN_AVAILABLE:
            sign_and_append(entry, AUDIT_LOG)
        else:
            with open(AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    except Exception:
        # Never fail - audit logging is best-effort
        pass


# CLI interface for testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        log_tool_call(
            session_id="test-session-123",
            tool_name="Bash",
            tool_input={"command": "git status"},
            decision="allow"
        )
        print(f"Test entry written to {AUDIT_LOG}")

        # Show last entry
        if AUDIT_LOG.exists():
            lines = AUDIT_LOG.read_text().strip().split("\n")
            if lines:
                print(f"Last entry: {lines[-1]}")

    elif len(sys.argv) > 1 and sys.argv[1] == "tail":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        if AUDIT_LOG.exists():
            lines = AUDIT_LOG.read_text().strip().split("\n")
            for line in lines[-n:]:
                print(line)
        else:
            print("No audit log yet")

    elif len(sys.argv) > 1 and sys.argv[1] == "stats":
        if AUDIT_LOG.exists():
            lines = AUDIT_LOG.read_text().strip().split("\n")
            print(f"Total entries: {len(lines)}")

            # Count by tool
            tools = {}
            for line in lines:
                try:
                    entry = json.loads(line)
                    tool = entry.get("tool", "unknown")
                    tools[tool] = tools.get(tool, 0) + 1
                except Exception:
                    pass

            print("\nBy tool:")
            for tool, count in sorted(tools.items(), key=lambda x: -x[1]):
                print(f"  {tool}: {count}")
        else:
            print("No audit log yet")

    elif len(sys.argv) > 1 and sys.argv[1] == "rotate":
        # Force rotation
        if not AUDIT_LOG.exists():
            print("No log file to rotate")
        else:
            size_mb = AUDIT_LOG.stat().st_size / (1024 * 1024)
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
            archive_path = ARCHIVE_DIR / f"api-audit-{timestamp}.jsonl"
            shutil.move(str(AUDIT_LOG), str(archive_path))
            print(f"Rotated {size_mb:.2f}MB to {archive_path}")

    elif len(sys.argv) > 1 and sys.argv[1] == "archives":
        # List archived logs
        if not ARCHIVE_DIR.exists():
            print("No archives yet")
        else:
            archives = sorted(ARCHIVE_DIR.glob("api-audit-*.jsonl"))
            if not archives:
                print("No archives yet")
            else:
                print(f"Archives in {ARCHIVE_DIR}:\n")
                total_size = 0
                for archive in archives:
                    size_mb = archive.stat().st_size / (1024 * 1024)
                    total_size += size_mb
                    print(f"  {archive.name}  ({size_mb:.2f} MB)")
                print(f"\nTotal: {len(archives)} files, {total_size:.2f} MB")

    elif len(sys.argv) > 1 and sys.argv[1] == "size":
        # Show current log size
        if AUDIT_LOG.exists():
            size_mb = AUDIT_LOG.stat().st_size / (1024 * 1024)
            print(f"Current log: {size_mb:.2f} MB (rotates at {MAX_LOG_SIZE_MB} MB)")
        else:
            print("No log file yet")

    else:
        print("Usage:")
        print("  python api_audit.py test     - Write test entry")
        print("  python api_audit.py tail N   - Show last N entries")
        print("  python api_audit.py stats    - Show statistics")
        print("  python api_audit.py size     - Show current log size")
        print("  python api_audit.py rotate   - Force rotation now")
        print("  python api_audit.py archives - List archived logs")
