"""Is a path inside a checkout of GRIP's own repository?

Hooks that send text to an outside model share one boundary: text from a GRIP
checkout is GRIP's own, so it may go out once the redaction shield has scrubbed
it; text from anywhere else (a private repository, a legal folder, a client
project, a scratch directory) stays on this machine. It is an allowlist, so a
place nobody listed counts as private, and every failure answers "not GRIP".
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

# The origin of every GRIP checkout: the shared tree, its worktrees, any clone.
GRIP_ORIGIN_RE = re.compile(r"[:/]grip-platform/GRIP(?:\.git)?/?$", re.IGNORECASE)


def git(cwd: Path, *args: str) -> tuple[int, str]:
    """(exit code, stdout) of a git command run in ``cwd``; (-1, "") when git cannot run."""
    try:
        done = subprocess.run(["git", "-C", str(cwd), *args],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return -1, ""
    return done.returncode, done.stdout.strip()


def grip_checkout_root(path: str | Path) -> Path | None:
    """The top of the GRIP checkout that contains ``path``, or None.

    ``path`` may be a directory or a file, including one not written yet. None
    also when git cannot answer, so a caller deciding whether text may leave
    fails closed. Git resolves a symlinked path itself, so ``~/.claude`` works.
    """
    start = Path(path).expanduser()
    if not start.is_dir():
        start = start.parent
    code, top = git(start, "rev-parse", "--show-toplevel")
    if code != 0 or not top:
        return None
    code, origin = git(Path(top), "remote", "get-url", "origin")
    return Path(top) if code == 0 and GRIP_ORIGIN_RE.search(origin) else None
