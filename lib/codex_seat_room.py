"""Whether a Codex seat has room to finish a job, before the ladder starts it there.

A seat the ladder calls ready can still be too full to finish a job. Codex then runs
out mid-job and falls into that workspace's credit pool: it is refused when the pool
is empty, and billed when it is not. Measured on Team seats, 2026-10-11/12:

- a 168,000-token GPT-6 Sol job took one seat's five-hour window from 16% to 100%
  and its weekly window from 31% to 47%, then died (session 813d40d8);
- a job that died at 44,669 tokens had taken another seat from 86% to 100%.

That is about 2,000 to 3,200 tokens per point of the five-hour window, and about
10,500 per point of the weekly window. This module takes the dearer rate.

V>> approved "room to finish" in the /hitl round of 2026-10-12. The ladder calls this
from ``_rung1_eligible`` and skips a seat only on exit code 3 (``NO_ROOM_EXIT``). Not 1:
that is Python's own code for an uncaught error, so a crash would have read as "no
room" and skipped a healthy seat (AGORA's falsifier on GRIP#8185). The windows come from
the seat's newest session file: the rate-limit snapshot Codex writes on every run,
the same one the ladder's ``classify_last_run`` reads. So the check spawns nothing,
spends nothing and never touches a reset credit, and a dry run still executes
nothing. The snapshot is as fresh as the seat's last run, and a window whose reset
has passed counts as empty. A seat with no readable snapshot is NOT skipped, so a
failed read never blocks a launch.

Job size, in tokens of model use: ``--size``, else ``$GRIP_CODEX_JOB_SIZE``, else a
small default. A council or a review seat stays well under it, and it skips only a
nearly full seat. A large job should declare its size.

Standard library only, and it writes no records. The ladder runs it with the system
``python3``, as it runs its own helpers; ``rules/never-system-python-for-grip.md``
allows that only for code that signs nothing, so keep it that way.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

#: The dearer of the two measured rates, so a job is never under-sized.
TOKENS_PER_FIVE_HOUR_POINT = 2_000
TOKENS_PER_WEEKLY_POINT = 10_500
#: An undeclared job is assumed this large: enough to skip a seat at 90% or more,
#: small enough never to starve a council or review seat.
DEFAULT_JOB_SIZE = 20_000
SIZE_ENV = "GRIP_CODEX_JOB_SIZE"
CEILING = 100.0
#: The seat that holds paid credits is only used while it stays under this, so a job
#: never runs it dry into its credit pool (session 813d40d8's rule).
CREDIT_SEAT_CEILING = 70.0
#: A snapshot names each window by its length in minutes.
WINDOW_NAMES = {300: "five_hour", 10080: "weekly"}
#: The only exit code that skips a seat. 1 is Python's uncaught-error code and 2 is
#: argparse's, so neither a crash nor a bad call can read as "no room".
NO_ROOM_EXIT = 3


def _used(window, now: float):
    """Percent used, 0 once the window's reset has passed, or None when unknown."""
    if not isinstance(window, dict) or not isinstance(window.get("used_percent"), (int, float)):
        return None
    resets = window.get("resets_at")
    if resets and resets <= now:
        return 0.0
    return float(window["used_percent"])


def verdict(seat: dict, size: int, now: float) -> tuple:
    """``(True, why)`` when the seat can take a job of ``size`` tokens, else
    ``(False, why)``. An unknown window never refuses: only a known shortfall skips."""
    ceiling = CREDIT_SEAT_CEILING if seat.get("has_credits") else CEILING
    windows = seat.get("windows") or {}
    needs = (("five-hour", windows.get("five_hour"), size / TOKENS_PER_FIVE_HOUR_POINT),
             ("weekly", windows.get("weekly"), size / TOKENS_PER_WEEKLY_POINT))
    for name, window, points in needs:
        used = _used(window, now)
        if used is not None and used + points > ceiling:
            return (False, f"{name} window at {used:.0f}% plus about {points:.0f} points "
                           f"for {size:,} tokens passes {ceiling:.0f}%")
    return (True, f"room for about {size:,} tokens")


def job_size(declared) -> int:
    """The job size in tokens: declared, else ``$GRIP_CODEX_JOB_SIZE``, else the default."""
    for value in (declared, os.environ.get(SIZE_ENV)):
        try:
            size = int(value)
        except (TypeError, ValueError):
            continue
        if size > 0:
            return size
    return DEFAULT_JOB_SIZE


def _snapshot_in(line: str):
    """The last rate-limit snapshot inside one session-file line, or None."""
    try:
        stack = [json.loads(line)]
    except ValueError:
        return None
    found = None
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if "rate_limit_reached_type" in current:
                found = current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


def _has_windows(snapshot: dict) -> bool:
    """True when a snapshot carries a subscription window, five-hour or weekly."""
    return any(isinstance(snapshot.get(key), dict)
               and snapshot[key].get("window_minutes") in WINDOW_NAMES
               for key in ("primary", "secondary"))


def _snapshot(home) -> dict:
    """The last rate-limit snapshot that carries subscription windows, in the seat's
    newest session file. Every snapshot names ``rate_limit_reached_type``, even when
    it is null (counted 2026-10-12 in six real seats' newest files: 1 to 105 lines
    each). A run that fell into the credit pool ends with that pool's snapshot
    (``limit_id`` "premium", no windows), so the last snapshot alone would show a
    spent seat as unknown; the subscription's windows sit in the snapshots before it."""
    sessions = Path(home).expanduser() / "sessions"
    files = sorted(sessions.rglob("rollout-*.jsonl"), key=lambda path: path.stat().st_mtime,
                   reverse=True) if sessions.is_dir() else []
    if not files:
        raise LookupError("no session file")
    snapshot = None
    for line in files[0].read_text(errors="replace").splitlines():
        found = _snapshot_in(line)
        if found is not None and _has_windows(found):
            snapshot = found
    if snapshot is None:
        raise LookupError("no rate-limit snapshot with subscription windows")
    return snapshot


def seat_from(snapshot: dict) -> dict:
    """``{"windows": {"five_hour": ..., "weekly": ...}, "has_credits": bool}``."""
    windows = {"five_hour": None, "weekly": None}
    for key in ("primary", "secondary"):
        window = snapshot.get(key)
        if isinstance(window, dict) and window.get("window_minutes") in WINDOW_NAMES:
            windows[WINDOW_NAMES[window["window_minutes"]]] = window
    credits = snapshot.get("credits")
    return {"windows": windows, "has_credits": isinstance(credits, dict) and credits.get("has_credits") is True}


def check(home, size: int, read=None) -> tuple:
    """``(True or False, why)`` for one seat; a seat with no readable snapshot answers True."""
    read = read or (lambda seat_home: seat_from(_snapshot(seat_home)))
    try:
        seat = read(home)
    except (OSError, LookupError, ValueError) as exc:
        return (True, f"windows unreadable ({type(exc).__name__}); not skipped")
    return verdict(seat, size, time.time())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", required=True, help="the seat's CODEX_HOME")
    parser.add_argument("--size", help="the job's size in tokens of model use, if known")
    args = parser.parse_args(argv)
    if os.environ.get("GRIP_CODEX_ROOM_CHECK", "1") == "0":
        print("room check switched off (GRIP_CODEX_ROOM_CHECK=0)")
        return 0
    try:
        room, why = check(args.home, job_size(args.size))
    except Exception as exc:  # noqa: BLE001 — a broken check must never skip a seat
        print(f"room check failed ({type(exc).__name__}); not skipped")
        return 0
    print(why)
    return 0 if room else NO_ROOM_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
