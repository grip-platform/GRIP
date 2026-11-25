#!/usr/bin/env python3
"""
user_detection.py -- Canonical user identity detection for GRIP.

Single source of truth for determining the current operator.
6-tier fallback: GRIP_USER env > identity.md > git config > os.getlogin > $USER > "unknown"

Usage:
    from lib.user_detection import get_username
    user = get_username()  # e.g. "laurie"
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
IDENTITY_FILE = CLAUDE_DIR / "facts" / "identity.md"

# Cache to avoid repeated I/O within a session
_cached_username: str | None = None


def get_username() -> str:
    """Detect the current GRIP operator username.

    6-tier fallback (fastest to slowest):
    1. GRIP_USER environment variable (explicit override)
    2. identity.md name field
    3. git config user.name (first name, lowercased)
    4. os.getlogin()
    5. $USER environment variable
    6. "unknown" (final fallback)

    Result is cached for the session.
    """
    global _cached_username
    if _cached_username is not None:
        return _cached_username

    username = _detect_username()
    _cached_username = username
    return username


def _detect_username() -> str:
    """Run through the 6-tier fallback. Returns lowercase first name."""
    # Tier 1: explicit env
    env_user = os.environ.get("GRIP_USER")
    if env_user:
        return env_user.strip().lower()

    # Tier 2: identity.md
    name = _parse_identity_name()
    if name:
        return name

    # Tier 3: git config
    name = _git_config_name()
    if name:
        return name

    # Tier 4: os.getlogin
    try:
        login = os.getlogin()
        if login:
            return login.strip().lower()
    except OSError:
        pass

    # Tier 5: $USER env
    user_env = os.environ.get("USER")
    if user_env:
        return user_env.strip().lower()

    # Tier 6: final fallback
    return "unknown"


def _parse_identity_name() -> str | None:
    """Extract first name from facts/identity.md."""
    if not IDENTITY_FILE.exists():
        return None
    try:
        for line in IDENTITY_FILE.read_text().splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("name:") or stripped.startswith("- name:"):
                # .strip("\"'") removes YAML-quoted-string delimiters so that
                # `name: "Laurie"` parses to `laurie` not `"laurie"`. Without
                # this, the literal quotes survive into the operator name and
                # leak into filenames like data/genomes/"laurie".json (#1378).
                name = stripped.split(":", 1)[1].strip().strip("\"'")
                # Take first name only
                first = name.split()[0] if name else ""
                return first if first else None
    except (UnicodeDecodeError, OSError):
        pass
    return None


def _git_config_name() -> str | None:
    """Get first name from git config user.name."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            full = result.stdout.strip()
            first = full.split()[0].lower()
            return first if first else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def clear_cache() -> None:
    """Clear the cached username (for testing)."""
    global _cached_username
    _cached_username = None
