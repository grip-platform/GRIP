"""Which Claude Code session, model and branch are running this process.

A record GRIP writes for a later reader should say who wrote it. Claude Code
exports the session id to every command it runs (``CLAUDE_CODE_SESSION_ID``),
and that session's transcript names the model that answered. ``current()``
gathers both, with the entry point and the git branch, so a writer can stamp a
row with its author (V>> 2026-11-16: a hypothesis must show where it came from).

Every field is best effort. An unknown field is the empty string, and nothing
here raises, so a caller can never fail because its provenance was unknown.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from claude_dir import get_claude_dir  # noqa: E402

# How much of the transcript's end is read to find the answering model. The
# last assistant record names it, and a turn rarely writes more than this.
TAIL_BYTES = 256 * 1024
GIT_TIMEOUT_S = 2

_MODEL = re.compile(r'"model"\s*:\s*"(claude-[A-Za-z0-9.\-]+)"')


def session_id(env: Optional[Mapping[str, str]] = None) -> str:
    """The Claude Code session id this process runs under, or ``""``."""
    src = os.environ if env is None else env
    return (src.get("CLAUDE_CODE_SESSION_ID") or "").strip()


def transcript_path(sid: str, root: Optional[Path] = None) -> Optional[Path]:
    """The session's transcript file under ``projects/``, or None."""
    if not sid:
        return None
    base = root if root is not None else get_claude_dir() / "projects"
    hits = sorted(base.glob(f"*/{sid}.jsonl"))
    return hits[0] if hits else None


def _tail(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - TAIL_BYTES))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


def session_model(sid: str, root: Optional[Path] = None) -> str:
    """The model id the session's last answer names, such as ``claude-opus-5-5``."""
    path = transcript_path(sid, root)
    if path is None:
        return ""
    found = _MODEL.findall(_tail(path))
    return found[-1] if found else ""


def git_branch(cwd: Optional[Path] = None) -> str:
    """The checked-out branch of the working directory's repository, or ``""``."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd, capture_output=True, text=True,
            timeout=GIT_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def current(env: Optional[Mapping[str, str]] = None,
            root: Optional[Path] = None) -> dict:
    """Session, model, entry point and branch of the running process."""
    src = os.environ if env is None else env
    sid = session_id(src)
    return {
        "session": sid,
        "model": session_model(sid, root),
        "entrypoint": (src.get("CLAUDE_CODE_ENTRYPOINT") or "").strip(),
        "branch": git_branch(),
    }
