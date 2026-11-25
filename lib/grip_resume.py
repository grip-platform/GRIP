#!/usr/bin/env python3
"""grip-resume — L3 true zero-touch trigger for the ultraclear warm-reload.

The operator runs ``grip-resume`` (typically from a SECOND pane) instead of manually
typing ``/clear`` then a go-signal. It send-keys ``/clear`` into a TARGET Claude Code
pane, waits for the warm-reload to stage the Next Step, then send-keys the trigger:

  * default        -> ``continue``  (L1: resume the single Next Step)
  * --autonomous   -> ``/afk``      (L2: autonomous multi-step resume, /afk safety core)

Targeting is SAFE BY CONSTRUCTION. It delivers only to an explicit Otty pane id (or the
single unambiguous agent pane). It NEVER sends to an empty/focused pane. The OSA
focused-window fallback is an explicit opt-in (``--osa-focus``, macOS only) with a loud
warning, and passes the keystroke text as an argv to osascript (parameterized — never
interpolated), so a trigger containing quotes cannot break or inject AppleScript.

Autonomy bounds are entirely L2/afk's (the four HALT classes still stop, 12h TTL,
grip-yolo relaxes only hesitation, the grip-stop brake halts outbound). This wrapper
ONLY delivers trigger keystrokes; it grants no authority of its own.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

DEFAULT_DELAY = 4.0  # seconds: /clear + SessionStart warm-reload settle before the trigger


class ResumeError(RuntimeError):
    """A resume could not be delivered safely (no target, ambiguous, or send-keys off)."""


def trigger_for(autonomous: bool, override: Optional[str]) -> str:
    """The keystroke line to submit after /clear. Override wins; else /afk (autonomous)
    or continue (single-step)."""
    if override:
        return override
    return "/afk" if autonomous else "continue"


def _pane_id_of(pane: dict) -> str:
    """Best-effort pane-id extraction across otty pane-list schemas."""
    for key in ("id", "pane_id", "paneId", "uuid"):
        val = pane.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def resolve_pane(explicit: Optional[str], *, transport) -> str:
    """Resolve the target Otty pane id — explicit wins; else the SINGLE agent pane.

    Ambiguity (0 or >1 agent panes) is a loud error demanding an explicit --pane, never
    a guess. An explicit id is stripped and returned directly; the transport's own
    send_prompt re-guards against an empty id as the final backstop (so this module does
    not depend on any private transport method)."""
    if explicit and explicit.strip():
        return explicit.strip()
    ids = [pid for pid in (_pane_id_of(p) for p in transport.agent_panes()) if pid]
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise ResumeError(
            "no Otty agent pane found — pass --pane <id> (see `otty pane list`)."
        )
    raise ResumeError(
        f"{len(ids)} agent panes are open — ambiguous. Pass --pane <id> explicitly "
        "so the trigger cannot land in the wrong session."
    )


def plan(autonomous: bool, trigger_override: Optional[str], *, delay: float) -> list:
    """The ordered plan (for --dry-run and tests): clear, wait, trigger."""
    return [("send", "/clear"), ("sleep", delay), ("send", trigger_for(autonomous, trigger_override))]


def resume_otty(
    pane: str,
    autonomous: bool,
    *,
    trigger_override: Optional[str] = None,
    delay: float = DEFAULT_DELAY,
    transport,
    sleep_fn: Callable[[float], None] = time.sleep,
    dry_run: bool = False,
) -> list:
    """Deliver /clear then the trigger to a resolved Otty pane. Returns the plan.

    ``bracketed=False`` is deliberate: /clear and the trigger are slash/word commands
    that must land as raw keystrokes, not a bracketed paste (which agent TUIs treat as a
    single input block rather than a submitted command)."""
    steps = plan(autonomous, trigger_override, delay=delay)
    if dry_run:
        return steps
    trigger = trigger_for(autonomous, trigger_override)
    transport.send_prompt(pane, "/clear", submit=True, bracketed=False)
    sleep_fn(delay)
    transport.send_prompt(pane, trigger, submit=True, bracketed=False)
    return steps


def resume_osa(
    autonomous: bool,
    *,
    trigger_override: Optional[str] = None,
    delay: float = DEFAULT_DELAY,
    sleep_fn: Callable[[float], None] = time.sleep,
    run_fn: Callable[[list], object] = subprocess.run,
    warn_fn: Callable[[str], None] = lambda m: print(m, file=sys.stderr),
    dry_run: bool = False,
) -> list:
    """macOS opt-in fallback: keystroke into the FOCUSED window. Risky — types wherever
    focus is; loud warning + the operator must keep this terminal focused.

    Injection-proof: the keystroke text is passed as an argv to osascript
    (``on run argv`` -> ``item 1 of argv``), never interpolated into the script body, so a
    trigger containing quotes/backslashes cannot break or inject AppleScript."""
    if sys.platform != "darwin":
        raise ResumeError("--osa-focus is macOS-only (needs osascript).")
    trigger = trigger_for(autonomous, trigger_override)
    steps = plan(autonomous, trigger_override, delay=delay)
    warn_fn(
        f"[grip-resume] OSA focus-typing in {delay:.0f}s — KEEP THIS TERMINAL FOCUSED. "
        "It types into whatever window is frontmost. Ctrl-C to abort."
    )
    if dry_run:
        return steps

    def _type(line: str) -> None:
        # Parameterized: `line` is a trailing argv element, NOT interpolated into an -e
        # body. A quote/backslash in `line` cannot escape or inject the AppleScript.
        run_fn(["osascript",
                "-e", "on run argv",
                "-e", 'tell application "System Events" to keystroke (item 1 of argv)',
                "-e", 'tell application "System Events" to key code 36',  # 36 = Return
                "-e", "end run",
                line])

    sleep_fn(delay)
    _type("/clear")
    sleep_fn(delay)
    _type(trigger)
    return steps


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grip-resume",
        description="Zero-touch resume: send-keys /clear then a resume trigger to a Claude Code pane.",
    )
    p.add_argument("--autonomous", action="store_true",
                   help="send /afk instead of continue (L2 autonomous multi-step resume).")
    p.add_argument("--pane", default=None, help="explicit Otty pane id (safest target).")
    p.add_argument("--trigger", default=None, help="override the trigger line (default: continue, or /afk).")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                   help=f"seconds between /clear and the trigger (default {DEFAULT_DELAY}).")
    p.add_argument("--osa-focus", action="store_true",
                   help="macOS fallback: keystroke into the FOCUSED window (risky, opt-in).")
    p.add_argument("--dry-run", action="store_true", help="print the plan; send nothing.")
    return p


def main(argv: Optional[list] = None, *, transport=None) -> int:
    args = build_parser().parse_args(argv)

    # OSA path — explicit opt-in only; needs no Otty transport.
    if args.osa_focus:
        try:
            steps = resume_osa(args.autonomous, trigger_override=args.trigger,
                               delay=args.delay, dry_run=args.dry_run)
        except ResumeError as exc:
            print(f"grip-resume: {exc}", file=sys.stderr)
            return 2
        _print_plan(steps, "OSA focused-window")
        return 0

    # Otty path — the safe default. Requires Otty + send-keys enabled.
    if transport is None:
        try:
            import otty_transport as transport  # lazy: only the Otty path needs it
        except Exception as exc:  # import failure must not crash — safe by construction
            print(f"grip-resume: Otty transport unavailable ({exc}). Use --osa-focus "
                  "(macOS, risky) or resume manually: /clear then continue (or /afk).",
                  file=sys.stderr)
            return 2
    if not transport.is_available():
        print("grip-resume: Otty not available. Use --osa-focus (macOS, risky) or "
              "resume manually: /clear then type continue (or /afk).", file=sys.stderr)
        return 2
    if not transport.send_keys_enabled():
        print("grip-resume: Otty send-keys is disabled. Enable it once with "
              "`grip-otty doctor --fix`, then retry.", file=sys.stderr)
        return 2
    try:
        pane = resolve_pane(args.pane, transport=transport)
        steps = resume_otty(pane, args.autonomous, trigger_override=args.trigger,
                            delay=args.delay, transport=transport, dry_run=args.dry_run)
    except ResumeError as exc:
        print(f"grip-resume: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # otty transport errors -> plain-language, non-crashing (Rule 17)
        print(f"grip-resume: could not deliver to Otty pane: {exc}", file=sys.stderr)
        return 1
    _print_plan(steps, f"Otty pane {pane}")
    return 0


def _print_plan(steps: list, target: str) -> None:
    trig = next((v for k, v in steps if k == "send" and v != "/clear"), "?")
    print(f"grip-resume → {target}: /clear, wait, {trig}")


if __name__ == "__main__":
    raise SystemExit(main())
