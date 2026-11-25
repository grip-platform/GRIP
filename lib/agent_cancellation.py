"""
Agent cancellation mechanism for GRIP.

File-based cancellation protocol for background agents. When a cancel file
exists at ~/.claude/.cancel-agents, agents should check periodically and
gracefully exit their loops.

Reference: Issue #70, Recommendation R7.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any


CANCEL_FILE = Path.home() / ".claude" / ".cancel-agents"


def check_cancelled(cancel_path: Optional[Path] = None) -> bool:
    """
    Check if agents have been requested to cancel.

    Args:
        cancel_path: Override path for testing. Defaults to ~/.claude/.cancel-agents.

    Returns:
        True if cancellation has been requested.
    """
    path = cancel_path or CANCEL_FILE
    return path.exists()


def cancel_all(
    reason: str = "",
    cancel_path: Optional[Path] = None,
) -> Path:
    """
    Request all agents to cancel by creating the cancel file.

    Args:
        reason: Human-readable reason for cancellation.
        cancel_path: Override path for testing.

    Returns:
        Path to the created cancel file.
    """
    path = cancel_path or CANCEL_FILE
    payload = {
        "requested_at": time.time(),
        "reason": reason,
        "pid": os.getpid(),
    }
    path.write_text(json.dumps(payload))
    return path


def get_cancel_reason(cancel_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    Read the cancellation details if a cancel has been requested.

    Args:
        cancel_path: Override path for testing.

    Returns:
        Dict with cancellation details, or None if not cancelled.
    """
    path = cancel_path or CANCEL_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"requested_at": 0, "reason": "unknown", "pid": 0}


def acknowledge_cancel(cancel_path: Optional[Path] = None) -> bool:
    """
    Acknowledge and clear the cancellation request by removing the file.

    Args:
        cancel_path: Override path for testing.

    Returns:
        True if the file was removed, False if it didn't exist.
    """
    path = cancel_path or CANCEL_FILE
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
