"""GRIP product adapter.

GRIP is the harness itself (``~/.claude``). Its install is probe-only: GRIP
installs and upgrades itself, so the adapter reports whether it is present and
whether its shared tree is canonical (on main and level), and never
auto-installs.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from ..installers import Target
from ..model import Check, Status
from ..run import TIMEOUT_MISSING, run

# Resolve the install dir via the canonical shim (path-portability.md).
_LIB = Path(__file__).resolve().parent.parent.parent  # lib/
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

NAME = "grip"
_TICK_STALE_S = 3600.0  # a tick log older than an hour is stale


def _claude_dir() -> Path:
    return get_claude_dir()


def _installed_probe() -> "tuple[Status, str]":
    claude_dir = _claude_dir()
    if (claude_dir / "CLAUDE.md").exists():
        return Status.REGISTERED, str(claude_dir)
    return Status.MISSING, f"no CLAUDE.md under {claude_dir}"


def _tree_probe() -> "tuple[Status, str]":
    check = _claude_dir() / "bin" / "grip-tree-check"
    res = run([str(check)], timeout=20)
    if res.code == 0:
        return Status.REGISTERED, "shared tree on main and level"
    if res.code == TIMEOUT_MISSING:
        return Status.UNKNOWN, "grip-tree-check not found"
    return Status.MISSING, f"tree not canonical (exit {res.code})"


def targets() -> list[Target]:
    return [
        Target("claude-code", _installed_probe, None),
        Target("tree", _tree_probe, None),
    ]


def _tick_check() -> Check:
    log = _claude_dir() / "logs" / "grip-tick.jsonl"
    if not log.exists():
        return Check(f"{NAME}::tick-fresh", False, "no grip-tick.jsonl")
    age = time.time() - log.stat().st_mtime
    return Check(f"{NAME}::tick-fresh", age < _TICK_STALE_S, f"last tick {int(age)}s ago")


def checks() -> list[Check]:
    return [_tick_check()]
