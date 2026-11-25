"""Per-launch activation of the never-ending cycle (ADR-0016 Step 7, W1).

OFF unless ONE launch asks for it. ``bin/grip`` runs this helper only when the
launch sets ``GRIP_INFINITE_CYCLE=1``, and it adds the cycle's two hooks to that
session's own ``--settings`` object and nowhere else:

* Stop: ``hooks/stop-idle-marker.py --arm-cycle --claude-pid "$PPID"``
* SessionStart, matcher ``clear``: ``hooks/infinite-cycle-reload.py --claude-pid "$PPID"``

Never in ``settings.json``, ``config/settings.reference.json`` or
``config/hooks.registry.json``: a merge touching the settings reference rewrites
every live session's settings, and default-on (decision D5) waits for the
operator's manual QA. A test fails if any shared settings file ever names these
hooks.

Not armed, with one visible line saying why, when:

* the brake (``bin/grip-infinite-stop``) is engaged. The watcher and the Stop
  hook also check it, so pulling the brake mid-session stops the cycle too;
* the launch brought its own ``--settings``. Claude Code would then have to
  choose between two, so the cycle stays off rather than guess;
* the hooks are missing from this checkout.

The hooks are named by absolute path from THIS checkout, so a launch from a
worktree runs the worktree's cycle and the shared install runs its own. They
run through ``lib/grip-python.sh``, the same venv-resolving wrapper every hook
in ``settings.json`` uses.

Fail-open: any error prints nothing on stdout, and the launch goes ahead
exactly as it would have without the cycle.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

ENV_OPT_IN = "GRIP_INFINITE_CYCLE"
ROOT = _LIB.parent
STOP_HOOK = "stop-idle-marker.py"
RELOAD_HOOK = "infinite-cycle-reload.py"
#: The names a shared settings file must never carry (pinned by a test).
HOOK_FILES = (STOP_HOOK, RELOAD_HOOK)


@dataclass(frozen=True)
class Arming:
    """Whether this launch gets the cycle, why, and the hooks block if it does."""

    armed: bool
    reason: str
    hooks: dict = field(default_factory=dict)


def hook_commands(root: Path = ROOT) -> dict:
    """The ``hooks`` block for one session's ``--settings``.

    ``"$PPID"`` is expanded by the shell Claude Code runs each hook in, so it is
    Claude Code's own pid, exactly as ``hooks/otty-state-hook.sh`` receives it.
    """
    runner = shlex.quote(str(root / "lib" / "grip-python.sh"))
    stop = shlex.quote(str(root / "hooks" / STOP_HOOK))
    reload_ = shlex.quote(str(root / "hooks" / RELOAD_HOOK))
    return {
        "Stop": [{"hooks": [{"type": "command",
                             "command": f'bash {runner} {stop} --arm-cycle --claude-pid "$PPID"'}]}],
        "SessionStart": [{"matcher": "clear", "hooks": [{
            "type": "command", "command": f'bash {runner} {reload_} --claude-pid "$PPID"'}]}],
    }


def decide_arming(env: Mapping[str, str], *, brake_engaged: bool, caller_settings: bool,
                  root: Path = ROOT) -> Arming:
    """Should this launch carry the cycle? Pure: reads only the files it names."""
    if env.get(ENV_OPT_IN) != "1":
        return Arming(False, f"{ENV_OPT_IN} is not 1 (the cycle is opt-in per launch)")
    if brake_engaged:
        return Arming(False, "the brake is engaged")
    if caller_settings:
        return Arming(False, "this launch brought its own --settings, and a second one "
                             "would leave Claude Code to pick between them")
    needed = (root / "hooks" / STOP_HOOK, root / "hooks" / RELOAD_HOOK,
              root / "lib" / "grip-python.sh")
    missing = [str(p) for p in needed if not p.is_file()]
    if missing:
        return Arming(False, f"missing from this checkout: {', '.join(missing)}")
    return Arming(True, "armed", hook_commands(root))


def merge_settings(base: str, hooks: Mapping) -> str:
    """``base`` (the launch's own settings JSON, or "") with ``hooks`` added.

    Hooks already in ``base`` are kept and the cycle's are appended per event,
    so the merge never drops anything the launch was carrying.
    """
    data = json.loads(base) if base.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("the launch's --settings is not a JSON object")
    merged = dict(data.get("hooks") or {})
    for event, groups in hooks.items():
        merged[event] = list(merged.get(event) or []) + list(groups)
    data["hooks"] = merged
    return json.dumps(data, separators=(",", ":"))


def notice(arming: Arming, env: Mapping[str, str]) -> str:
    """The one visible line this launch owes the operator."""
    if not arming.armed:
        return f"[grip-cycle] not armed: {arming.reason}"
    from infinite_cycle_econ import CycleConfig
    return f"[grip-cycle] armed for this session only: {CycleConfig.from_env(env).describe()}"


def _brake_engaged() -> bool:
    from grip_infinite import brake_state
    return brake_state().engaged


def main(argv: Optional[list] = None, env: Optional[Mapping[str, str]] = None) -> int:
    """``settings [--merge JSON] [--caller-settings]``: print the merged JSON, or nothing."""
    args = list(sys.argv[1:] if argv is None else argv)
    environ = os.environ if env is None else env
    try:
        if not args or args[0] != "settings":
            sys.stderr.write("usage: infinite_cycle_launch.py settings [--merge JSON] "
                             "[--caller-settings]\n")
            return 2
        base = args[args.index("--merge") + 1] if "--merge" in args[:-1] else ""
        arming = decide_arming(environ, brake_engaged=_brake_engaged(),
                               caller_settings="--caller-settings" in args)
        sys.stderr.write(notice(arming, environ) + "\n")
        if arming.armed:
            print(merge_settings(base, arming.hooks))
    except Exception as exc:  # noqa: BLE001 - the cycle is an extra, never a gate
        sys.stderr.write(f"[grip-cycle] not armed: the helper failed ({type(exc).__name__})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
