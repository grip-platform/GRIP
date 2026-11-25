#!/usr/bin/env python3
"""Session-scoped task namespace for GRIP.

Claude Code's TaskCreate/TaskList tools store tasks at the project level,
not the session level. When concurrent sessions run, both see the same
task list. This module provides session-scoped task namespacing.

Fix for #769: Task list leaks across concurrent sessions.

Usage:
    from task_namespace import scoped_subject, is_own_task, session_prefix

    # Creating a task
    subject = scoped_subject("W1.2: Fix KONO checkpoint pollution")
    # -> "[a7b2] W1.2: Fix KONO checkpoint pollution"

    # Checking ownership
    is_own_task("[a7b2] W1.2: Fix KONO ...")  # True (if current session)
    is_own_task("[c3d4] W3.1: Implement ...")  # False (different session)
"""

import hashlib
import os
import re
from pathlib import Path

_PREFIX_RE = re.compile(r"^\[([a-f0-9]{4})\]\s*")


def _resolve_session_id() -> str:
    """Get current session ID from env or breadcrumb."""
    env_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if env_id:
        return env_id

    ppid = os.getppid()
    sessions_dir = Path.home() / ".claude" / ".grip-sessions"
    breadcrumb = sessions_dir / f"sid-{ppid}"
    if breadcrumb.exists():
        try:
            return breadcrumb.read_text().strip()
        except OSError:
            pass

    # Fallback: use PID-based hash for uniqueness
    return f"pid-{os.getpid()}"


def session_prefix() -> str:
    """Return the 4-char hex prefix for the current session."""
    sid = _resolve_session_id()
    return hashlib.sha256(sid.encode()).hexdigest()[:4]


def scoped_subject(subject: str) -> str:
    """Prefix a task subject with session-scoped namespace.

    If already prefixed (from a previous call), returns as-is.
    """
    if _PREFIX_RE.match(subject):
        return subject
    return f"[{session_prefix()}] {subject}"


def is_own_task(subject: str) -> bool:
    """Check if a task subject belongs to the current session."""
    match = _PREFIX_RE.match(subject)
    if not match:
        return True  # Unscoped tasks are assumed to be own
    return match.group(1) == session_prefix()


def strip_prefix(subject: str) -> str:
    """Remove the session prefix from a task subject."""
    return _PREFIX_RE.sub("", subject)
