"""HAL Home — resolve the Knowledge Engine's home directory.

Priority:
1. HAL_HOME env var (explicit override)
2. ~/.hal (standalone default)
3. CLAUDE_DIR env var (backward compat when running inside GRIP)
4. ~/.claude (legacy fallback)

Usage:
    from lib.hal.home import HAL_HOME
"""
import os

_hal_path = os.path.expanduser("~/.hal")
_claude_path = os.path.expanduser("~/.claude")

HAL_HOME: str = os.environ.get(
    "HAL_HOME",
    _hal_path if os.path.isdir(_hal_path) else os.environ.get("CLAUDE_DIR", _claude_path),
)


def is_cc_session() -> bool:
    """Check if running inside an external harness session (e.g. Claude Code)."""
    return bool(os.environ.get("CLAUDE_CODE_SESSION_ID"))
