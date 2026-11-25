"""Where GRIP keeps tools it installs OUTSIDE every repository.

A tool server pinned to an upstream commit (stealth-browser-mcp is the first)
gets its own checkout and its own virtual environment. Neither belongs inside
``~/.claude``: a branch switch or a worktree removal would delete a running
server's code, and a 117 MB venv has no place in a git tree. So they live under
one root, resolved here and nowhere else:

    $GRIP_TOOLS_DIR  ->  $HOME/.grip-tools

Layout per tool: ``<root>/<tool>/<sha12>/{repo,.venv,.grip-pin}`` and a
``<root>/<tool>/current`` symlink naming the active ``<sha12>``. The installer
(``bin/grip-harness-install``, manager ``git-venv``) writes it; the launcher
(``lib/grip_browser_surface.py``) and the CLI (``lib/grip_browser_cli.py``) read
it. Stdlib only, no I/O beyond the environment, so a launcher run under a tool
server's own interpreter can import it.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT_ENV = "GRIP_TOOLS_DIR"
PIN_FILE = ".grip-pin"
CURRENT = "current"


def tools_root(env: dict | None = None) -> Path:
    """``$GRIP_TOOLS_DIR`` when set and non-blank, else ``$HOME/.grip-tools``."""
    environ = os.environ if env is None else env
    override = (environ.get(ROOT_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    home = (environ.get("HOME") or "").strip() if env is not None else ""
    base = Path(home).expanduser() if home else Path.home()
    return base / ".grip-tools"


def tool_root(tool: str, env: dict | None = None) -> Path:
    """``<tools root>/<tool>`` -- the directory holding every installed sha."""
    return tools_root(env) / tool


def current_dir(tool: str, env: dict | None = None) -> Path:
    """The active install: ``<tool root>/current`` (a symlink to a ``<sha12>``)."""
    return tool_root(tool, env) / CURRENT


def sha_dir(tool: str, sha: str, env: dict | None = None) -> Path:
    """The install directory for one pinned sha (first 12 hex characters)."""
    return tool_root(tool, env) / sha[:12]


def resolve_current(tool: str, env: dict | None = None) -> Path | None:
    """The directory ``current`` names, or None when nothing is active.

    ``current`` is a symlink where the platform allows one, and a one-line text
    file holding the ``<sha12>`` where it does not (Windows without the
    symlink privilege). Both resolve here; the launcher's bash does the same.
    """
    marker = current_dir(tool, env)
    try:
        if marker.is_symlink() or marker.is_dir():
            target = marker.resolve()
            return target if target.is_dir() else None
        if marker.is_file():
            name = marker.read_text(encoding="utf-8").strip()
            target = marker.parent / name
            return target if name and target.is_dir() else None
    except OSError:
        return None
    return None


def set_current(tool: str, sha12: str, env: dict | None = None) -> Path:
    """Point ``current`` at ``<sha12>`` atomically: a symlink built under a
    temporary name and renamed over the old one, so a reader never sees a
    missing or half-written marker; a text file where symlinks are refused."""
    root = tool_root(tool, env)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / CURRENT
    tmp = root / f".{CURRENT}.{os.getpid()}.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    try:
        os.symlink(sha12, tmp)
    except (OSError, NotImplementedError):
        tmp.write_text(sha12 + "\n", encoding="utf-8")
    if marker.is_dir() and not marker.is_symlink():
        raise OSError(f"{marker} is a real directory, not a marker; refusing to replace it")
    os.replace(tmp, marker)
    return marker


def write_pin(install_dir: Path, sha: str) -> Path:
    """Record the installed sha; written whole, then renamed into place."""
    pin = install_dir / PIN_FILE
    tmp = install_dir / f"{PIN_FILE}.{os.getpid()}.tmp"
    tmp.write_text(sha.strip() + "\n", encoding="utf-8")
    os.replace(tmp, pin)
    return pin


def read_pin(install_dir: Path) -> str | None:
    """The full sha recorded by the installer, or None when there is no pin."""
    try:
        text = (install_dir / PIN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def venv_python(install_dir: Path) -> Path | None:
    """The venv interpreter in either layout (POSIX ``bin/``, Windows ``Scripts/``)."""
    for rel in ("bin/python", "Scripts/python.exe"):
        candidate = install_dir / ".venv" / rel
        if candidate.is_file():
            return candidate
    return None
