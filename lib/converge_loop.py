"""Closed-loop convergence runner — Fibonacci depth + threaded lessons.

The kernel that turns ``lib/converge_lessons.py`` into a *loop*. Each wave
runs at an increasing Fibonacci depth (1, 1, 2, 3, 5, 8 ...), and — crucially
— the runner threads the **folded prior lessons** into every wave's executor
call. Wave N sees what waves 1..N-1 learned and can act on it. That threading
IS the closed loop the charter asks for: "depth forced to increase in
Fibonacci series with automatic learning from previous convergence loop and
applying the fixes or enhancements".

The kernel brings no intelligence of its own
--------------------------------------------
``run_waves`` makes **no LLM calls**. The ``executor`` is DIP-injected and
brings the intelligence (an agent, a model, a deterministic fixer, a test
runner). The kernel only: computes the depth ladder, loads+threads prior
lessons, records each wave's lesson, and stops on criterion-met or
``max_waves``. Pure-python, deterministic, fully unit-testable with a fake
executor.

Executor contract
-----------------
``executor(wave_index: int, depth: int, prior_lessons: list[dict]) -> dict``
returns at least::

    {"outcome": "<short status>", "lesson": "<what this wave learned>"}

and may add ``"fixed": bool``, ``"falsifier": str``, ``"criterion_met":
bool``. ``criterion_met`` True stops the loop after this wave (the work is
done — no need to burn the remaining Fibonacci budget).

Depth ladder
------------
``fibonacci_depth(0) == 1``, ``fibonacci_depth(1) == 1``, then 2, 3, 5, 8 —
the canonical breathing sequence GRIP's dfi-fib ladder uses. Pure function.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Reuse the lessons substrate (DRY — one fold, one store). DIP: the runner
# accepts ``load``/``append`` overrides so tests bind a temp path with zero
# global state.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import converge_lessons as _cl  # noqa: E402

#: A wave executor: (wave_index, depth, prior_lessons) -> result dict.
Executor = Callable[[int, int, list], dict]

__all__ = ["fibonacci_depth", "run_waves", "WaveRecord", "LoopResult"]


def fibonacci_depth(wave_index: int) -> int:
    """Depth for wave ``wave_index`` along the 1, 1, 2, 3, 5, 8 ... ladder.

    Pure + total: index 0 and 1 both map to 1 (the doubled Fibonacci base),
    thereafter each depth is the sum of the prior two. Negative indices clamp
    to 1 (defensive; the runner never passes one).
    """
    if wave_index <= 1:
        return 1
    a, b = 1, 1
    for _ in range(wave_index - 1):
        a, b = b, a + b
    return b


@dataclass(frozen=True)
class WaveRecord:
    """One wave's immutable outcome — what ran, at what depth, what it learned."""

    wave_index: int
    depth: int
    outcome: str
    lesson: str
    fixed: bool
    criterion_met: bool
    prior_count: int  # how many folded prior lessons this wave was threaded


@dataclass
class LoopResult:
    """The whole loop's result: per-wave records + stop reason."""

    target: str
    waves: list[WaveRecord] = field(default_factory=list)
    criterion_met: bool = False
    waves_run: int = 0


def _normalise(result: dict) -> tuple[str, str, bool, bool, str]:
    """Pull the executor's contract fields with safe defaults.

    A malformed/partial result never crashes the loop: ``outcome``/``lesson``
    fall back to plain strings, the booleans to False, ``falsifier`` to "".
    """
    outcome = str(result.get("outcome", "")) if isinstance(result, dict) else ""
    lesson = str(result.get("lesson", "")) if isinstance(result, dict) else ""
    fixed = bool(result.get("fixed", False)) if isinstance(result, dict) else False
    met = bool(result.get("criterion_met", False)) if isinstance(result, dict) else False
    falsifier = str(result.get("falsifier", "")) if isinstance(result, dict) else ""
    return outcome, lesson, fixed, met, falsifier


def run_waves(
    target: str,
    executor: Executor,
    max_waves: int,
    *,
    path: Path | None = None,
    load=_cl.load_lessons,
    append=_cl.append_lesson,
) -> LoopResult:
    """Run up to ``max_waves`` waves of the closed loop against ``target``.

    Each wave: (1) compute its Fibonacci depth; (2) load the folded prior
    lessons for ``target`` and **thread them into the executor** — the closed
    loop; (3) run the executor; (4) record its lesson via ``append`` so the
    NEXT wave sees it; (5) stop early if the executor reports
    ``criterion_met``.

    ``load``/``append`` are DIP seams (default to the real lessons store);
    ``path`` is forwarded to both so a test binds a temp file. Returns a
    :class:`LoopResult` with one :class:`WaveRecord` per wave that ran.
    """
    result = LoopResult(target=target)
    for wave_index in range(max(0, max_waves)):
        depth = fibonacci_depth(wave_index)
        prior = load(target, path=path)
        raw = executor(wave_index, depth, prior)
        outcome, lesson, fixed, met, falsifier = _normalise(raw)
        append(
            f"{target}-w{wave_index}", target, depth, outcome, lesson,
            falsifier, path=path,
        )
        result.waves.append(WaveRecord(
            wave_index=wave_index, depth=depth, outcome=outcome, lesson=lesson,
            fixed=fixed, criterion_met=met, prior_count=len(prior),
        ))
        if met:
            result.criterion_met = True
            break
    result.waves_run = len(result.waves)
    return result
