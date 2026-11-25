"""Would clearing this session save anything? The never-ending cycle's dry run.

ADR-0016 Decision 2: the W1 cycle is "gated by an /ultraclear dry run (clear
only when it actually saves something)". This module is that dry run. It reuses
the /ultraclear planner (``lib/ultraclear.py``) for WHAT a warm reload carries,
the HOT manifest, and measures everything else from the session's own
transcript instead of estimating it. It never writes anything.

Why not /ultraclear's own ``net_savings``? That figure compares one reprocess of
the window (``window x 0.10``) with one write of the manifest
(``manifest x 1.25``), which assumes a fresh session carries nothing but the
manifest. A GRIP session does not start empty: on this machine's recent
transcripts (measured 2026-10-05), the first turn of an interactive session
already carried roughly 97,000 to 223,000 tokens of rules, memory and hook
context. A clear re-caches all of that, so a formula that leaves it out reports
savings a clear cannot deliver. The cycle must never clear on an overstated
number, so it measures that floor rather than assuming it is zero.

The model, using the same Anthropic prompt-cache multipliers /ultraclear uses
(a cache read costs 0.10 of the base input price, a cache write 1.25):

    window  = the context the next turn re-reads   (the latest usage record)
    floor   = the context this session started at  (the first usage record)
    after   = floor + HOT manifest + continuity block
              (what the window holds right after a clear and warm reload)
    freed   = window - after                       (no longer re-read every turn)
    premium = after x (1.25 - 0.10)
              one-time: the first turn after the clear WRITES ``after`` to the
              cache instead of READING ``window`` from it
    saving  = freed x 0.10                         per turn, every turn after that
    payback = premium / saving                     turns until the clear has paid
                                                   for itself

THE THRESHOLD. A clear fires only when ``freed > 0`` AND ``payback <= N`` turns.
N defaults to ``DEFAULT_MAX_BREAKEVEN_TURNS`` (10) and is set per launch with
``GRIP_INFINITE_CYCLE_BREAKEVEN_TURNS``. With a 100,000-token floor and a
20,000-token manifest, N=10 means the cycle fires once the window passes about
258,000 tokens: well before the 1M wall, and only when the clear is plainly
worth it. A smaller N is stricter; a larger N accepts a slower payback.

Three choices err toward NOT clearing, on purpose:

* ``premium`` assumes no cache hit on the floor across sessions. Some of the
  system prompt may be shared, but GRIP's session context differs per session,
  and how much is shared was not measured.
* the planner runs with ``kernel_tokens=0``: the holistic kernel is loaded at
  session start, so it is already inside the measured floor. Counting it again
  in the manifest would double it; leaving it at zero keeps ``after`` honest.
* a transcript with no usage record cannot be measured, and the dry run then
  refuses (``measure_window`` returns ``None``) instead of estimating.

The floor is the FIRST usage record, not the smallest. A subagent or an error
record can carry a tiny window, and taking the minimum would understate
``after`` and so overstate the saving.
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from infinite_cycle import DEFAULT_MIN_IDLE_S  # noqa: E402
from ultraclear import Turn, UltraclearConfig, plan_ultraclear  # noqa: E402

#: Anthropic prompt-cache multipliers, relative to the base input price. The
#: same figures /ultraclear inlines (``UltraclearConfig``).
READ_MULT = 0.10
WRITE_MULT = 1.25

#: A clear must pay for itself within this many turns. See the module docstring.
DEFAULT_MAX_BREAKEVEN_TURNS = 10.0
ENV_MAX_BREAKEVEN = "GRIP_INFINITE_CYCLE_BREAKEVEN_TURNS"
ENV_MIN_IDLE = "GRIP_INFINITE_CYCLE_MIN_IDLE_S"

#: The usage fields that together make up one request's context window.
_WINDOW_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


@dataclass(frozen=True)
class Window:
    """How large this session's context is now, and how large it started."""

    current: int
    floor: int
    samples: int


def usage_window(usage: object) -> int:
    """Context tokens one API request carried, from its ``usage`` block; 0 if none."""
    if not isinstance(usage, Mapping):
        return 0
    counts = (usage.get(key) for key in _WINDOW_KEYS)
    return sum(c for c in counts if isinstance(c, int) and not isinstance(c, bool) and c > 0)


def _line_window(line: str) -> int:
    """The window of one transcript line: main-chain assistant turns only."""
    try:
        record = json.loads(line)
    except ValueError:
        return 0
    if not isinstance(record, dict) or record.get("type") != "assistant":
        return 0
    if record.get("isSidechain") is True:
        return 0
    message = record.get("message")
    return usage_window(message.get("usage")) if isinstance(message, dict) else 0


def measure_window(transcript: object) -> Optional[Window]:
    """The session's current and starting window, or ``None`` when unmeasurable.

    ``None`` is the honest answer for a missing file or a transcript with no
    usage record: the caller then refuses rather than guessing a size.
    """
    try:
        with Path(str(transcript)).open(encoding="utf-8", errors="replace") as handle:
            sizes = [size for size in map(_line_window, handle) if size > 0]
    except OSError:
        return None
    if not sizes:
        return None
    return Window(current=sizes[-1], floor=sizes[0], samples=len(sizes))


def _positive_env(env: Optional[Mapping[str, str]], name: str, default: float) -> float:
    """A positive finite number from the environment, else ``default``.

    A value that is not one falls back to the default rather than switching a
    gate off. Every record names the thresholds it used, so a typo shows up
    there instead of passing silently.
    """
    raw = (os.environ if env is None else env).get(name, "")
    try:
        value = float(str(raw).strip())
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def max_breakeven_turns(env: Optional[Mapping[str, str]] = None) -> float:
    """The payback threshold for this launch (``GRIP_INFINITE_CYCLE_BREAKEVEN_TURNS``)."""
    return _positive_env(env, ENV_MAX_BREAKEVEN, DEFAULT_MAX_BREAKEVEN_TURNS)


@dataclass(frozen=True)
class CycleConfig:
    """The cycle's thresholds and timings, read once per launch from its environment.

    The two thresholds an operator tunes per launch: ``min_idle_s``
    (``GRIP_INFINITE_CYCLE_MIN_IDLE_S``, default ``DEFAULT_MIN_IDLE_S`` = 90 s
    of quiet after the last Stop) and ``max_payback_turns`` (see the module
    docstring). The rest are the watcher's mechanics: how often it looks, how
    long it waits for a clear to come back, and how long it may live.
    """

    min_idle_s: float = DEFAULT_MIN_IDLE_S
    max_payback_turns: float = DEFAULT_MAX_BREAKEVEN_TURNS
    poll_s: float = 5.0
    #: Measured 2026-10-05: the new session id appeared 10-20 s after /clear,
    #: while about 20 SessionStart hooks ran.
    clear_timeout_s: float = 90.0
    max_lifetime_s: float = 7 * 24 * 3600.0
    #: A declined idle period whose last W4 status was pending (paused, on a
    #: target, or the wire did not answer) stays eligible: cheaply repoll
    #: deps.w4_status alone, starting at this interval (GRIP#7720 Codex
    #: 4207339316) -- never the full evaluate() on a timer.
    w4_repoll_start_s: float = 60.0
    #: The repoll interval doubles each time it finds no relief, capped here.
    w4_repoll_max_s: float = 900.0

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "CycleConfig":
        return cls(min_idle_s=_positive_env(env, ENV_MIN_IDLE, DEFAULT_MIN_IDLE_S),
                   max_payback_turns=max_breakeven_turns(env))

    def describe(self) -> str:
        """The thresholds, in one sentence an operator can check."""
        return (f"clears only after {self.min_idle_s:g} s idle, and only when a dry run "
                f"shows the clear pays back within {self.max_payback_turns:g} turns OR "
                "HAL's wire reports a W4 capacity move (ADR-0016 Step 5f, GRIP#7720); "
                "bin/grip-infinite-stop (the brake) turns it off")


@dataclass(frozen=True)
class DryRun:
    """The dry run's verdict and every number behind it. Nothing was written."""

    window_tokens: int
    floor_tokens: int
    manifest_tokens: int
    continuity_tokens: int
    after_tokens: int
    freed_tokens: int
    premium: float
    saving_per_turn: float
    payback_turns: float
    max_payback_turns: float
    hot_turns: int
    cold_turns: int
    saves: bool
    reason: str

    def record(self) -> dict:
        """JSON-safe numbers for a log row or a decision record (no text)."""
        payback = None if math.isinf(self.payback_turns) else round(self.payback_turns, 2)
        return {
            "window_tokens": self.window_tokens, "floor_tokens": self.floor_tokens,
            "manifest_tokens": self.manifest_tokens,
            "continuity_tokens": self.continuity_tokens,
            "after_tokens": self.after_tokens, "freed_tokens": self.freed_tokens,
            "premium": round(self.premium, 1),
            "saving_per_turn": round(self.saving_per_turn, 1),
            "payback_turns": payback, "max_payback_turns": self.max_payback_turns,
            "hot_turns": self.hot_turns, "cold_turns": self.cold_turns,
            "saves": self.saves,
        }


def _verdict(window: Window, after: int, freed: int, payback: float,
             limit: float) -> tuple:
    """(saves, reason): the gate, and a sentence a person can check by hand."""
    if freed <= 0:
        return False, (f"no savings: the window ({window.current} tokens) is not larger "
                       f"than what a clear would reload ({after} tokens: the "
                       f"{window.floor}-token floor plus the manifest)")
    if payback > limit:
        return False, (f"savings too slow: a clear frees {freed} tokens per turn but "
                       f"pays back its re-cache only after {payback:.1f} turns "
                       f"(the limit is {limit:g})")
    return True, (f"saves: a clear frees {freed} tokens per turn and pays back its "
                  f"re-cache in {payback:.1f} turns (the limit is {limit:g})")


def dry_run(turns: Iterable[Turn], window: Window, *, max_payback_turns: float,
            continuity_tokens: int = 0,
            config: UltraclearConfig = UltraclearConfig()) -> DryRun:
    """Plan the clear without performing it, and say whether it pays.

    ``turns`` are the session's turns as ``lib/ultraclear_capture`` builds them;
    ``continuity_tokens`` is the size of the block the reload adds beside the
    HOT manifest, so the saving is measured against everything a clear reloads.
    """
    plan = plan_ultraclear(turns, kernel_tokens=0, config=config)
    extra = max(0, int(continuity_tokens))
    after = window.floor + plan.manifest_tokens + extra
    freed = window.current - after
    premium = after * (WRITE_MULT - READ_MULT)
    saving = freed * READ_MULT if freed > 0 else 0.0
    payback = premium / saving if saving > 0 else math.inf
    saves, reason = _verdict(window, after, freed, payback, max_payback_turns)
    return DryRun(
        window_tokens=window.current, floor_tokens=window.floor,
        manifest_tokens=plan.manifest_tokens, continuity_tokens=extra,
        after_tokens=after, freed_tokens=freed, premium=premium,
        saving_per_turn=saving, payback_turns=payback,
        max_payback_turns=max_payback_turns, hot_turns=len(plan.hot),
        cold_turns=len(plan.cold), saves=saves, reason=reason,
    )
