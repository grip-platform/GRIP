"""The fire gate GRIP's scheduled loops share.

A wave is dispatched only when the loop is enabled, inside its deadline, with no
STOP marker, no operator outbound brake engaged, and no wave already running.

Moved out of lib/overnight_loop.py on 2026-11-26 (GRIP#9981), so that public code
such as lib/stack_dogfood/loop.py can use the gate without importing the
overnight loop, which the public build withholds. lib/overnight_loop.py binds
these same names, so its callers and tests are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

OUTBOUND_STOP_MARKER = Path.home() / ".claude" / "state" / "session" / "outbound-stop.json"
# Same global brake lib/whatsapp_transport.py / lib/slack_transport.py /
# lib/grip_channel_transport.py / lib/voice_note.py already check
# (full-power-stop-override.md's "GRIP, full stop" safeword). The overnight
# wave previously had no awareness of it -- wired in via outbound_stop_engaged().


@dataclass(frozen=True)
class WaveDecision:
    fire: bool
    reason: str


def should_fire(cfg: dict | None, *, now_epoch: float,
                lock_held: bool, stop_present: bool,
                outbound_stop: bool = False) -> WaveDecision:
    """Pure gate: dispatch a wave IFF the loop is enabled, within its deadline,
    no STOP marker, no operator outbound-stop brake engaged, and no wave
    already running. Order is load-bearing — the two operator-halt signals
    short-circuit before the loop's own state so an operator halt always wins.

    ``outbound_stop`` (Phase 0 workhorse hardening): True when the operator's
    GLOBAL outbound brake is engaged (``grip-stop on`` / the spoken "GRIP
    stop" safeword — see OUTBOUND_STOP_MARKER / outbound_stop_engaged()).
    Defaults False so every existing caller that doesn't pass it keeps prior
    behaviour byte-identical; bin/grip-overnight-wave wires the live check.

    Mutation anchors (tests/test_overnight_loop.py,
    tests/test_overnight_loop_hardening.py): drop any clause and a
    controlled case flips fire True<->False.
    """
    if stop_present:
        return WaveDecision(False, "stop-marker present")
    if outbound_stop:
        return WaveDecision(False, "outbound-stop brake engaged")
    if not cfg or not cfg.get("enabled"):
        return WaveDecision(False, "loop disabled")
    deadline = cfg.get("deadline_epoch")
    if deadline is not None and now_epoch >= float(deadline):
        return WaveDecision(False, "past deadline")
    if lock_held:
        return WaveDecision(False, "wave already in progress")
    return WaveDecision(True, "fire")


def outbound_stop_engaged(marker: Path = OUTBOUND_STOP_MARKER) -> bool:
    """True iff the operator's global outbound brake is engaged.

    Shares the exact marker lib/whatsapp_transport.py, lib/slack_transport.py,
    lib/grip_channel_transport.py, and lib/voice_note.py already check
    (state/session/outbound-stop.json). The brake is meant to be GLOBAL
    (full-power-stop-override.md) -- this closes the gap where the overnight
    wave previously had no awareness of it.
    """
    return marker.exists()
