"""Check your computer: the suite installer's first step (GRIP Suite, W2).

Each part is a proof in plain words: the operating system is one the suite supports, this
Python is 3.11 or newer, Git is installed and actually runs, and the disk has room.

Named computer.py, not platform.py: a file in this folder run as a script puts the folder
first on the import path, and a platform.py here would then hide the standard library's
platform module.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .steps import Proof

MIN_PYTHON = (3, 11)
SYSTEMS = {"darwin": "macOS", "linux": "Linux", "win32": "Windows"}
# GRIP with its venv, HAL and GRASP, with room to spare.
NEED_BYTES = 2_000_000_000


def system_proof(name: str = sys.platform) -> Proof:
    for prefix, label in SYSTEMS.items():
        if name.startswith(prefix):
            return Proof(True, f"{label} is supported")
    return Proof(False, f"this system ({name}) is not supported; the suite runs on macOS, "
                        "Linux and Windows")


def python_proof(version=sys.version_info) -> Proof:
    found = f"{version[0]}.{version[1]}.{version[2]}"
    if tuple(version[:2]) >= MIN_PYTHON:
        return Proof(True, f"Python {found}")
    return Proof(False, f"Python {found} is too old; the suite needs Python 3.11 or newer")


def git_proof(which=shutil.which, run=subprocess.run) -> Proof:
    """Git must be found AND run: on a Mac without the developer tools, /usr/bin/git
    exists but only prints an error."""
    path = which("git")
    if path is None:
        return Proof(False, "Git is not installed")
    try:
        done = run([path, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Proof(False, f"Git is installed but does not run: {exc}")
    if done.returncode != 0:
        said = (done.stderr or done.stdout or "").strip()[:200]
        return Proof(False, f"Git is installed but does not run: {said}")
    return Proof(True, f"Git runs ({done.stdout.strip()})")


def _gb(count: int) -> str:
    return f"{count / 1_000_000_000:.1f} GB"


def disk_proof(path: Path, need_bytes: int = NEED_BYTES, usage=shutil.disk_usage) -> Proof:
    """A disk that cannot be read is a failed part with its reason, never a raise:
    a raise would replace every part of the computer check with one line."""
    try:
        free = usage(path).free
    except OSError as exc:
        return Proof(False, f"could not read the free disk space at {path}: {exc}")
    if free >= need_bytes:
        return Proof(True, f"{_gb(free)} of disk free")
    return Proof(False, f"only {_gb(free)} of disk free; the suite needs about {_gb(need_bytes)}")


def check_computer(home: Optional[Path] = None) -> Proof:
    """All four parts; it holds only when every part holds."""
    parts = (system_proof(), python_proof(), git_proof(), disk_proof(home or Path.home()))
    return Proof(all(part.ok for part in parts), "; ".join(part.detail for part in parts))
