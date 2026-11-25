#!/usr/bin/env python3
"""Should the never-ending cycle fire right now, and can it? — ADR-0002 wave W1.

W1's wave text reads like a from-scratch build ("BUILD marker producer +
idle-gated /clear watcher"). It mostly is not. Three of the four pieces already
existed, unwired:

  * the PRODUCER — ``lib/aide/wall_relief.py`` predicts the context wall and
    writes ``state/ultraclear-pending.json``. Its own docstring says it shipped
    deliberately ahead of a consumer;
  * the ACTUATOR — ``lib/grip_resume.py`` send-keys ``/clear`` into a target
    Claude Code pane and then a trigger, with safe-by-construction targeting
    (explicit pane id or the single unambiguous agent pane, never a focused or
    empty one);
  * the WARM RELOAD — ``hooks/ultraclear-reload-sessionstart.py``.

What was missing is the DECISION between them: given a pending marker, is the
session actually idle, is its checkpoint fresh enough to reload from, and is
there any way to deliver a keystroke at all.

The two thresholds are ``DEFAULT_MIN_IDLE_S`` (90s of quiet after the last Stop
event) and ``DEFAULT_MAX_CHECKPOINT_AGE_S`` (900s, past which a checkpoint is too
stale to reload from). Both are overridable per call, and named here so a caller
does not have to read the source to know what it is opting into.

That decision is pure on purpose. It takes timestamps and an actuator name and
returns a verdict — no clock, no filesystem, no subprocess — so every branch is
testable without a live session, and the eventual hook that calls it stays small
enough to review. Delivery is NOT here: this module never sends a keystroke.

The honest-degradation rule is the load-bearing one. A headless session, or one
with no reachable pane, CANNOT be cleared out-of-band — ``/clear`` cannot
self-invoke mid-tool-call, which is why the actuator exists at all. When relief
is warranted and undeliverable the verdict says so in ``degraded`` rather than
reporting a quiet ``fire=False``, because those two mean opposite things to an
operator: one is "nothing to do", the other is "something is needed and this
machine cannot do it".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

# An actuator can deliver a keystroke to a pane; NONE means this machine cannot.
ACTUATOR_OTTY = "otty"
ACTUATOR_TMUX = "tmux"
ACTUATOR_NONE = "none"

# Only these can deliver. Membership is checked rather than inequality against
# NONE, so an unknown string - a typo, or a future actuator this build does not
# know - is treated as UNDELIVERABLE rather than silently enabling delivery.
DELIVERABLE_ACTUATORS = frozenset({ACTUATOR_OTTY, ACTUATOR_TMUX})

DEFAULT_MIN_IDLE_S = 90.0
DEFAULT_MAX_CHECKPOINT_AGE_S = 900.0


@dataclass(frozen=True)
class CycleDecision:
    """Whether to fire, and why — `reason` is written to be shown to a human."""

    fire: bool
    reason: str
    actuator: str = ACTUATOR_NONE
    degraded: bool = False


def actuator_kind(*, otty_available: bool = False, tmux_available: bool = False,
                  env: Optional[Mapping[str, str]] = None) -> str:
    """Which actuator this machine can use, preferring the more precise one.

    Otty is preferred because it addresses a pane by id and can enumerate agent
    panes, so delivery is targeted. tmux is the fallback and needs an existing
    session (``$TMUX``), because send-keys outside one has nowhere to go.
    """
    if otty_available:
        return ACTUATOR_OTTY
    if tmux_available and (env or {}).get("TMUX"):
        return ACTUATOR_TMUX
    return ACTUATOR_NONE


def decide(
    marker: Optional[Mapping[str, object]],
    *,
    now: float,
    last_stop_at: Optional[float],
    checkpoint_at: Optional[float],
    actuator: str,
    min_idle_s: float = DEFAULT_MIN_IDLE_S,
    max_checkpoint_age_s: float = DEFAULT_MAX_CHECKPOINT_AGE_S,
) -> CycleDecision:
    """Decide whether the checkpoint→/clear→warm-reload cycle should fire.

    Order matters, and it is cheapest-and-most-conclusive first: a missing or
    non-pending marker means there is nothing to do at all, and asking about
    idleness or actuators then would be noise.

    The checkpoint check is what keeps this safe. Clearing a session whose
    checkpoint is stale would discard live context in favour of an old warm
    reload — worse than not firing — so a stale checkpoint refuses, and says so.
    """
    if not marker or marker.get("status") != "pending":
        return CycleDecision(False, "no pending relief marker", actuator)

    if last_stop_at is None:
        return CycleDecision(
            False, "no Stop event recorded yet - cannot tell whether the session is idle",
            actuator)

    idle_for = now - last_stop_at
    if idle_for < 0:
        # A Stop in the future is clock skew, not idleness. Refusing is the safe
        # reading: we cannot measure how long the session has been quiet, and
        # "-60s ago" is nonsense to show anyone (AGORA #5104).
        return CycleDecision(
            False,
            f"last Stop event is {-idle_for:.0f}s in the FUTURE - clock skew, "
            f"so idleness cannot be measured",
            actuator)
    if idle_for < min_idle_s:
        return CycleDecision(
            False,
            f"session went idle {idle_for:.0f}s ago, under the {min_idle_s:.0f}s gate",
            actuator)

    if checkpoint_at is None:
        return CycleDecision(
            False, "no checkpoint to warm-reload from - clearing would lose the context",
            actuator)

    checkpoint_age = now - checkpoint_at
    # A checkpoint in the future is clock skew, and it is certainly not STALE,
    # so it does not refuse - it is clamped to 0 and the reason says why. The
    # first version branched here instead and duplicated the actuator check
    # below, which is the sort of thing that drifts (AGORA #5104).
    skewed = checkpoint_age < 0
    if skewed:
        checkpoint_age = 0.0
    elif checkpoint_age > max_checkpoint_age_s:
        return CycleDecision(
            False,
            f"checkpoint is {checkpoint_age:.0f}s old, over the "
            f"{max_checkpoint_age_s:.0f}s freshness bound - clearing would lose work",
            actuator)

    if actuator not in DELIVERABLE_ACTUATORS:
        return CycleDecision(
            False,
            "relief is warranted but this session has no way to deliver /clear "
            "out-of-band (no Otty pane, no tmux session)",
            actuator, degraded=True)

    freshness = ("checkpoint written in the future (clock skew, treated as fresh)"
                 if skewed else f"checkpoint {checkpoint_age:.0f}s old")
    return CycleDecision(
        True,
        f"idle {idle_for:.0f}s, {freshness}, deliverable via {actuator}",
        actuator)
