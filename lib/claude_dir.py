"""CLAUDE_DIR portability shim.

Resolves the GRIP install directory with env-var override, so containerised /
CI / multi-user-server deployments can point GRIP at a non-home path without
patching code.

Canonical per ``rules/path-portability.md``:

    Use ``$HOME/.claude/`` or ``${CLAUDE_DIR:-$HOME/.claude}``.

This shim is the single source of truth for the resolution.

Resolution order
----------------
1. ``$CLAUDE_DIR`` environment variable (non-empty, post-strip) — wins.
2. ``$HOME/.claude`` (default, unchanged from prior behaviour).

Empty / whitespace-only ``CLAUDE_DIR`` falls back to the default so an
unintentional ``export CLAUDE_DIR=""`` does not break callers.

Backward compatibility
----------------------
Callers may continue to cache the result at module load time::

    from claude_dir import get_claude_dir
    CLAUDE_DIR = get_claude_dir()

This matches the pre-existing pattern in ``lib/grip_session_sync.py`` et al.
Tests that need fresh resolution (e.g. after mutating ``$CLAUDE_DIR`` mid-
test) should call ``get_claude_dir()`` directly rather than rely on the
cached constant.

Out of scope
------------
- Path injection / untrusted-input handling: the env var is operator-
  controlled, not an untrusted surface (per
  ``rules/untrusted-content-capability-lock.md``).
- Symlink resolution: ``Path.expanduser().resolve()`` resolves through
  symlinks transparently, which is the correct behaviour for a
  portability shim (symlinks should be invisible to callers).
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["get_claude_dir"]


def get_claude_dir() -> Path:
    """Resolve the GRIP install directory.

    Returns
    -------
    Path
        ``$CLAUDE_DIR`` (expanded + resolved) when set and non-empty;
        otherwise ``$HOME/.claude``.

    Examples
    --------
    Default (no env)::

        >>> import os; os.environ.pop("CLAUDE_DIR", None)
        >>> get_claude_dir() == Path.home() / ".claude"
        True

    Override::

        >>> os.environ["CLAUDE_DIR"] = "/opt/grip"
        >>> get_claude_dir() == Path("/opt/grip").resolve()
        True
    """
    env = os.environ.get("CLAUDE_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".claude"
