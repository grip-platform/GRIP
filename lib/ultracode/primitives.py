"""ultracode P1 — the four primitives over the kernel's injected-adapter seam.

dispatch (fan-out) · verify (monotone) · synthesize (exogenous) · recurse (refine-to-criterion).

Every primitive is provider/harness-agnostic by construction: it only ever touches an
injected `Adapter` (P0's DIP seam) plus injected pure functions (checker / judge / refine),
never a provider or a harness directly. When the injected adapter is `HALAdapter`, work
runs on GRIP's non-Anthropic HAL substrate (Pool B) — the kernel itself never knows.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .kernel import run, State


def dispatch(task: Any, adapter, cache: dict, n: int = 1) -> list:
    """Fan-out: execute `task` n independent ways through the injected adapter.

    Idempotent per leg (an identical leg short-circuits to the cache). Legs are made
    distinct by index so a fan-out of n explores n variations, not n identical calls.
    """
    out = []
    for i in range(n):
        leg = task if n == 1 else {"_task": task, "_leg": i}
        out.append(run(leg, State(), adapter, cache).output)
    return out


def verify(result: Any, criterion: Any,
           l1: Callable[[Any, Any], bool],
           l2: Optional[Callable[[Any, Any], bool]] = None) -> bool:
    """MONOTONE adversarial check (llm-augmentation-pattern).

    Passes iff the deterministic floor `l1` passes AND (the optional recall layer `l2`
    passes, or `l2` is absent). Because `l2` is AND-composed strictly AFTER `l1`, it can
    only ever TIGHTEN a verdict — turn a pass into a fail — never rescue an `l1` failure.
    Monotone toward the safe (DENY) verdict by construction.
    """
    if not l1(result, criterion):
        return False                      # deterministic floor: a DENY short-circuits
    if l2 is None:
        return True
    return bool(l2(result, criterion))    # may only turn True -> False, never False -> True


def synthesize(results: list, judge: Callable[[list], Any]) -> Any:
    """Exogenous consensus: an injected `judge` reduces N results to one.

    The judge is exogenous to the producers — no model grades its own homework
    (exogenous-anchor-law). Raises on an empty result set (there is nothing to judge).
    """
    if not results:
        raise ValueError("synthesize requires at least one result")
    return judge(results)


def recurse(task: Any, adapter, cache: dict,
            criterion: Any,
            l1: Callable[[Any, Any], bool],
            refine: Callable[[Any, Any], Any],
            l2: Optional[Callable[[Any, Any], bool]] = None,
            max_depth: int = 5) -> dict:
    """Re-dispatch a refined task until `verify()` passes or `max_depth` is hit.

    Returns ``{"output", "depth", "passed"}``. Always bounded by `max_depth` (never
    unbounded). `refine(current_task, last_output) -> next_task` is injected.
    """
    current = task
    out = None
    for depth in range(max_depth):
        out = run(current, State(), adapter, cache).output
        if verify(out, criterion, l1, l2):
            return {"output": out, "depth": depth, "passed": True}
        current = refine(current, out)
    return {"output": out, "depth": max_depth, "passed": False}
