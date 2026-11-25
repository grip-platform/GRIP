"""ultracode P2 — reasoning topologies over the P1 primitives.

Three strategies, selected by ``cfg["topology"]`` via :func:`solve`:

- **chain**  (Chain-of-Thought): a linear sequence of reasoning steps.
- **tree**   (Tree-of-Thought): beam search — branch, score, prune, deepen.
- **forest** (Forest-of-Thought): N independent trees, aggregated by an exogenous judge.

All three are provider/harness-agnostic (they only ever touch the injected `Adapter`,
through :func:`dispatch`) and inherit the kernel's content-hash idempotency (a repeated
run makes zero new adapter calls). Injected pure functions — ``step`` / ``expand`` /
``score`` / ``judge`` — keep every topology deterministic-testable.
"""
from __future__ import annotations

import itertools
from typing import Any, Callable

from .primitives import dispatch, synthesize


def chain(task: Any, adapter, cache: dict,
          step: Callable[[Any, int], Any], n_steps: int = 3) -> dict:
    """CoT: run ``n_steps`` sequential reasoning steps.

    Each step dispatches the current task, then ``step(output, i)`` produces the next task.
    Returns ``{"output", "trace", "topology"}``.
    """
    current, out, trace = task, None, []
    for i in range(n_steps):
        out = dispatch(current, adapter, cache, n=1)[0]
        trace.append(out)
        current = step(out, i)
    return {"output": out, "trace": trace, "topology": "chain"}


def _score_level(frontier: list, adapter, cache: dict,
                 expand: Callable[[Any], list], score: Callable[[Any], float]) -> list:
    """One level of expansion: dispatch + score every child of every frontier node.

    Flattened (no nested loops): expand -> children -> dispatch -> score -> zip.
    Returns ``[(score, child_task, output), ...]``.
    """
    children = list(itertools.chain.from_iterable(expand(t) for t in frontier))
    outputs = [dispatch(child, adapter, cache, n=1)[0] for child in children]
    return [(score(out), child, out) for child, out in zip(children, outputs)]


def tree(task: Any, adapter, cache: dict,
         expand: Callable[[Any], list], score: Callable[[Any], float],
         beam: int = 2, depth: int = 2) -> dict:
    """ToT: beam search.

    ``expand(task) -> [child_task, ...]`` branches; ``score(output) -> float`` ranks
    (higher is better). At each of ``depth`` levels, all children of the current frontier
    are dispatched, scored, and the top ``beam`` are kept. Returns the global best.
    """
    frontier = [task]
    best = None  # (score, task, output)
    for _ in range(depth):
        scored = _score_level(frontier, adapter, cache, expand, score)
        if not scored:
            break
        scored.sort(key=lambda x: x[0], reverse=True)
        if best is None or scored[0][0] > best[0]:
            best = scored[0]
        frontier = [child for _, child, _ in scored[:beam]]
    return {
        "output": best[2] if best else None,
        "score": best[0] if best else None,
        "topology": "tree",
    }


def forest(task: Any, adapter, cache: dict,
           expand: Callable[[Any], list], score: Callable[[Any], float],
           judge: Callable[[list], Any],
           n_trees: int = 3, beam: int = 2, depth: int = 2) -> dict:
    """FoT: run ``n_trees`` independent trees from distinctly-seeded roots, then aggregate.

    Each tree gets a distinct seed so the kernel's idempotency does not collapse them into
    one. The ``judge`` is exogenous to the trees (no tree grades its own homework —
    exogenous-anchor-law). Returns ``{"output", "roots", "topology"}``.
    """
    bests = [
        tree({"_seed": k, "_task": task}, adapter, cache, expand, score,
             beam=beam, depth=depth)["output"]
        for k in range(n_trees)
    ]
    return {"output": synthesize(bests, judge), "roots": bests, "topology": "forest"}


def solve(task: Any, adapter, cache: dict, cfg: dict) -> dict:
    """Unified entry point — route to a reasoning topology by ``cfg['topology']``.

    - ``chain``  needs ``cfg['step']`` (+ optional ``n_steps``).
    - ``tree``   needs ``cfg['expand']`` + ``cfg['score']`` (+ optional ``beam``/``depth``).
    - ``forest`` needs ``cfg['expand']`` + ``cfg['score']`` + ``cfg['judge']`` (+ options).
    """
    topo = cfg.get("topology", "chain")
    if topo == "chain":
        return chain(task, adapter, cache, cfg["step"], cfg.get("n_steps", 3))
    if topo == "tree":
        return tree(task, adapter, cache, cfg["expand"], cfg["score"],
                    cfg.get("beam", 2), cfg.get("depth", 2))
    if topo == "forest":
        return forest(task, adapter, cache, cfg["expand"], cfg["score"], cfg["judge"],
                      cfg.get("n_trees", 3), cfg.get("beam", 2), cfg.get("depth", 2))
    raise ValueError(f"unknown topology: {topo!r}")
