"""The tick's throttle flag as a launcher reads it: ``state/session/throttle.json``, raised by the always-on
tick (``lib/tick_throttle.py``, the act that writes and seals it) when the weekly Claude spend reaches the
operator's signed soft cap. What the flag means is decided by the operator (2026-10-29): a headless
GRIP-started run refuses to start while the flag is set, an interactive session prints one warning line
and runs.

This module only READS. It reaches no record writer, so a shebanged tool may import it without the re-exec
into the signing interpreter that ``tests/test_pq_reexec.py`` demands of anything that can seal a record,
and the shell's ``python3`` may run its command line. The readers: ``bin/grip`` at its Claude launch seam
(``grip_handoff``, through ``python3 lib/throttle_flag.py describe``), ``lib/headless_run.py`` before any
harness is made, ``lib/relay_dispatcher.py`` before any spawn, and, through ``refuse_if_set`` (which raises
``Throttled`` with ``describe``'s line), the Claude Code harness's ``as_argv`` (``lib/harness/cli.py``, so a
Claude guest inside another host is refused too), the worktree swarm coordinator before any worktree and again
before each atom's agent, so a flag raised mid-swarm stops the rest, the
judgement run (``bin/grip_judgement_run.py``, exit ``THROTTLED_EXIT``) and the sovereign council's Claude seat,
which becomes a dead speaker while the other seats answer (GRIP#8844). A run on a non-Anthropic model spends no
Claude limit and is not refused. Existence decides: an unreadable or malformed flag still throttles, and
``describe`` says so instead of raising. Deleting the file is the reversal.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from claude_dir import get_claude_dir  # noqa: E402

BY = "grip-tick"   # the writer the tick will clear; any other writer's flag is left alone
VERSION = 1
THROTTLED_EXIT = 75  # EX_TEMPFAIL, the exit a refused headless run gives, as lib/relay_dispatcher.py does


def flag_path() -> Path:
    return get_claude_dir() / "state" / "session" / "throttle.json"


def read_flag(path: Path) -> Optional[dict]:
    """The flag's body, or None when the file is absent or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_set(path: Optional[Path] = None) -> bool:
    """True while a flag file exists, readable or not: an unreadable flag still throttles."""
    return (path or flag_path()).exists()


def is_own(flag: Optional[dict]) -> bool:
    """True for a flag the tick wrote itself."""
    return flag is not None and flag.get("by") == BY


def _fields(flag: dict) -> Optional[Tuple[int, int]]:
    """(highest weekly percent, cap percent) from a flag's body, or None when either field is malformed."""
    try:
        worst = max((int(v) for v in (flag.get("weekly_pct") or {}).values()), default=0)
        return worst, int(flag.get("cap_pct") or 0)
    except (AttributeError, TypeError, ValueError):
        return None


def describe(path: Optional[Path] = None) -> str:
    """One line for a launcher: why headless GRIP runs refuse, or an empty string when no flag is set. A flag
    that cannot be read, or whose fields are malformed, still throttles: the line says so and names the remedy."""
    target = path or flag_path()
    if not target.exists():
        return ""
    flag = read_flag(target)
    fields = None if flag is None else _fields(flag)
    if fields is None:
        return f"throttle flag set at {target} (unreadable); remove it to launch"
    worst, cap = fields
    return (f"throttle flag set {flag.get('at', '?')}: weekly Claude spend {worst}% reached the {cap}% cap; "
            f"headless GRIP runs refuse until the weekly reset or `rm {target}`")


class Throttled(RuntimeError):
    """A headless GRIP-started Claude run refused to start because the flag is set; the message is ``describe``'s
    line, so whoever catches it can say why and what lifts it."""


def refuse_if_set(path: Optional[Path] = None) -> None:
    """Raise Throttled with ``describe``'s line while the flag is set; return quietly otherwise. The guard a spawn
    site calls before it starts a headless Claude run (V>>'s decision of 2026-10-29)."""
    if is_set(path):
        raise Throttled(describe(path))


def main(argv: Optional[List[str]] = None) -> int:
    """The launchers' reader. ``describe`` prints the line (nothing when no flag is set) and exits 0; ``is-set``
    exits 0 while a flag file exists and 1 otherwise. Neither writes a record."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["describe"]:
        line = describe()
        if line:
            print(line)
        return 0
    if args == ["is-set"]:
        return 0 if is_set() else 1
    print("usage: throttle_flag.py describe | is-set", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
