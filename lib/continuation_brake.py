"""Brakes on an autonomous continuation chain.

A continuation COUNTER was the only brake until now, and Astra's judgement on
it is correct: "continuation count measures neither progress nor exposure. One
continuation can do substantial damage; eleven may be legitimate incremental
work." A counter answers "how many turns" when the questions that matter are
"is it still getting anywhere" and "how long has it been running alone".

So there are three brakes, and they measure three different things:

  STALL     consecutive continuations that changed nothing in the repository.
            This is the spin loop the cap was really aiming at, measured
            directly instead of by proxy.

  BUDGET    wall-clock seconds since the FIRST continuation of the chain.
            This is exposure: how long the machine has been acting with
            nobody watching. Critically, the number is not invented here --
            when the operator has declared AFK with a TTL, THAT is the
            sanctioned window, because they said so. Without a declaration
            the default is deliberately short, since somebody may be waiting.

  CAP       a plain counter, kept and raised rather than replaced.

Keeping the cap is the part Astra's argument does not cover. STALL depends on
a progress probe, and a probe that wrongly reports progress -- a stray write
from another process, a log line, a peer session touching the shared tree --
disables the brake that depends on it, silently. The cap needs nothing to be
working. It is the backstop for a broken sensor, which is why it survives as
an outer bound even though it is the weakest of the three as a primary brake.

Every threshold is injected. Nothing here reads a clock or a repository.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# The cap is no longer the primary brake, so it sits where it only catches a
# chain that STALL and BUDGET both failed to stop -- i.e. a broken sensor.
DEFAULT_CAP = 40

# Consecutive no-change continuations before the chain is called stalled.
# Three, not one: a single turn spent reading before writing is ordinary work.
DEFAULT_STALL_LIMIT = 3

# Used only when the operator has NOT declared a window. Short on purpose:
# an undeclared chain is one somebody may be waiting on.
DEFAULT_BUDGET_SECONDS = 2 * 60 * 60


class Brake(Enum):
    """Why a chain stopped. NONE means it may continue."""

    NONE = "none"
    STALL = "stall"
    BUDGET = "budget"
    CAP = "cap"


@dataclass
class Chain:
    """The state of one session's continuation chain."""

    started_at: float | None = None
    used: int = 0
    stalled: int = 0
    last_fingerprint: str | None = None
    history: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "used": self.used,
            "stalled": self.stalled,
            "last_fingerprint": self.last_fingerprint,
            "history": self.history[-10:],
        }

    @staticmethod
    def from_dict(row: object) -> "Chain":
        """Tolerate anything, including the bare integer this replaces.

        The old state file was `{session_id: 7}`. A reader that raised on it
        would take every running session down at the moment of the upgrade --
        the exact failure `_from_row` was written for a few hours ago, and the
        reason this is the second place tolerating an unknown shape today.
        """
        if isinstance(row, (int, float)):
            return Chain(used=int(row))
        if not isinstance(row, dict):
            return Chain()
        known = {"started_at", "used", "stalled", "last_fingerprint", "history"}
        kw = {k: v for k, v in row.items() if k in known}
        kw.setdefault("history", [])
        if not isinstance(kw.get("history"), list):
            kw["history"] = []
        return Chain(**kw)


def budget_seconds(afk_remaining: float | None, default: float = DEFAULT_BUDGET_SECONDS) -> float:
    """The sanctioned window for a chain.

    An operator who set `/afk ... 8 hours` has authorised eight hours of
    unattended work IN THEIR OWN WORDS. Inventing a shorter number here would
    override a declaration with a guess -- the same error as reading someone's
    availability off a clock. A declared window that has already expired is
    not a window, so it falls back to the default rather than to zero: the
    chain is then bounded by the default, not instantly killed.
    """
    if afk_remaining is not None and afk_remaining > 0:
        return float(afk_remaining)
    return float(default)


def chain_for_turn(chain: Chain, last_human_at: float | None) -> Chain:
    """The chain this Stop belongs to: a fresh one once the operator has spoken.

    BUDGET measures time spent acting with nobody watching, and STALL counts
    self-set continuations in a row. A prompt the operator types ends both
    measurements: whoever typed it was watching, and the run of steps the
    session set itself is over. Without this a session's first chain lasted
    its whole life -- 45 hours in the case that found it (2026-10-12), with
    `stalled` at 5 -- so every later `(mine)` closer met a spent budget and a
    stale stall count, and the stop went through with its reason on a stderr
    line the terminal never shows.

    `last_human_at` is the transcript's timestamp of the latest prompt a person
    typed; hook feedback and tool results are not prompts. None, or a chain
    that has not started, leaves the chain as it is.
    """
    if chain.started_at is None or last_human_at is None:
        return chain
    return Chain() if last_human_at > chain.started_at else chain


def assess(
    chain: Chain,
    fingerprint: str,
    now: float,
    *,
    budget: float = DEFAULT_BUDGET_SECONDS,
    cap: int = DEFAULT_CAP,
    stall_limit: int = DEFAULT_STALL_LIMIT,
) -> tuple[Brake, Chain]:
    """Decide whether the chain may continue, and return its next state.

    `fingerprint` is an opaque summary of the repository -- equal means
    nothing changed since the last continuation. It is never interpreted
    here, which is what keeps this function free of any repository.

    Order matters and is deliberate. STALL is checked first because a spinning
    chain should stop on the evidence that it is spinning, not later when a
    clock happens to run out; reporting the accurate reason is the whole point
    of having three brakes instead of one number.
    """
    nxt = Chain(
        started_at=chain.started_at if chain.started_at is not None else now,
        used=chain.used + 1,
        stalled=chain.stalled,
        last_fingerprint=fingerprint,
        history=list(chain.history),
    )

    if chain.last_fingerprint is not None and fingerprint == chain.last_fingerprint:
        nxt.stalled = chain.stalled + 1
    else:
        nxt.stalled = 0

    if nxt.stalled >= stall_limit:
        nxt.history.append("stall")
        return Brake.STALL, nxt

    elapsed = now - (chain.started_at if chain.started_at is not None else now)
    if elapsed >= budget:
        nxt.history.append("budget")
        return Brake.BUDGET, nxt

    if chain.used >= cap:
        nxt.history.append("cap")
        return Brake.CAP, nxt

    nxt.history.append("continue")
    return Brake.NONE, nxt


def explain(brake: Brake, chain: Chain, *, budget: float, cap: int, stall_limit: int) -> str:
    """One plain sentence naming why the chain stopped, for the transcript."""
    if brake is Brake.STALL:
        return (
            f"STOPPING — {chain.stalled} continuations in a row changed nothing in "
            f"the repository (limit {stall_limit}). The chain is spinning, not working."
        )
    if brake is Brake.BUDGET:
        hours = budget / 3600.0
        return (
            f"STOPPING — this continuation chain has been running unattended for "
            f"{hours:.1f} hours, which is the whole sanctioned window."
        )
    if brake is Brake.CAP:
        return (
            f"STOPPING — {chain.used} continuations, the outer cap ({cap}). Neither the "
            "stall nor the time brake fired, so one of them may be broken; that is "
            "what this cap is for."
        )
    return ""
