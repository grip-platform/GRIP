"""The session marker that keeps the operator's `normal mode` through a compaction.

rules/canonical-output-style.md says that `normal mode`, `stop caveman` and
`stop adhd mode` restore full prose for the session. No code detected those phrases,
so a compaction or a resume printed the SessionStart directive again and the choice
was lost. hooks/full-prose-marker-hook.py now writes this marker when the operator
types one of the phrases, and hooks/caveman-session-default.py adds one line after
the directive on `compact` and `resume`. The directive itself always prints: the
phrases change the register, never the report and safety contracts.

The phrases match only as a whole prompt, so a prompt that merely mentions one does
not switch anything. A later `/caveman` command is a newer choice, so it clears the
marker. A marker older than MAX_AGE_DAYS is removed at the next write.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

try:
    from .claude_dir import get_claude_dir  # noqa: PLC0415
except ImportError:
    from claude_dir import get_claude_dir  # type: ignore[no-redef]  # noqa: PLC0415

#: The phrases that restore full prose for a session.
ESCAPE_PHRASES = ("normal mode", "stop caveman", "stop adhd mode")
#: The command that chooses a compression level, and so overrides the phrases.
COMPRESSION_COMMAND = "/caveman"
MAX_AGE_DAYS = 30
_SAFE_SESSION = re.compile(r"[A-Za-z0-9_-]{1,128}")


def is_escape(prompt) -> bool:
    """Whether the whole prompt is one of the phrases, ignoring case and a final stop."""
    return isinstance(prompt, str) and prompt.strip().rstrip(".!").lower() in ESCAPE_PHRASES


def is_compression(prompt) -> bool:
    """Whether the prompt chooses a compression level with `/caveman`."""
    words = prompt.strip().lower().split() if isinstance(prompt, str) else []
    return bool(words) and words[0] == COMPRESSION_COMMAND


def marker_dir() -> Path:
    return Path(get_claude_dir()) / "state" / "session" / "full-prose"


def marker_path(session_id):
    """The marker file of a session, or None for an id that is unsafe as a file name."""
    if isinstance(session_id, str) and _SAFE_SESSION.fullmatch(session_id):
        return marker_dir() / session_id
    return None


def _remove_old(directory: Path, now: float) -> None:
    for path in directory.iterdir():
        if now - path.stat().st_mtime > MAX_AGE_DAYS * 86400:
            path.unlink(missing_ok=True)


def write_marker(session_id, now: float | None = None) -> bool:
    """Record the session's choice. False when the session id cannot name a file."""
    path = marker_path(session_id)
    if path is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    _remove_old(path.parent, time.time() if now is None else now)
    path.touch()
    return True


def clear_marker(session_id) -> None:
    """Forget the session's choice, after a newer `/caveman` command."""
    path = marker_path(session_id)
    if path is not None:
        path.unlink(missing_ok=True)


def has_marker(session_id) -> bool:
    path = marker_path(session_id)
    return path is not None and path.exists()
