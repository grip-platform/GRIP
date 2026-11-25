"""ultracode P0 — content-addressed idempotent kernel foundation.

The state-hashing storage layer + idempotent execution (the sovereign council's
"single highest-leverage first step": `grip-sovereign-council --verify bf75f66e72ccdba5`).

Two invariants, both by construction:

1. **Idempotent-by-design.** Work only ever happens through `run()`, which
   content-hashes `(task, state)` and short-circuits to the cache. Re-running an
   identical `(task, state)` makes ZERO new adapter calls (Banach: f∘f = f).

2. **Provider/harness-agnostic.** The ONLY way a real computation enters the kernel
   is through an injected `Adapter` (dependency inversion). P0 injects a deterministic
   adapter; P1 injects a HAL provider adapter (Pool B); a harness adapter speaks HAPPI.
   The kernel itself never imports a provider or a harness.

P1 (next) implements the four primitives whose contracts are declared here as
Protocols: dispatch / verify (monotone) / synthesize (exogenous) / recurse.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol


def content_hash(obj: Any) -> str:
    """Deterministic sha256 over a canonical JSON encoding.

    Order-independent for dicts (sort_keys), so semantically-equal inputs hash equal.
    This is the addressing function for the whole DAG — the idempotency backbone.
    """
    canon = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Node:
    """An immutable node in the content-addressed DAG. `id` == content_hash(input)."""
    id: str
    input: Any
    output: Any = None
    children: tuple = ()


@dataclass(frozen=True)
class State:
    """An immutable content-addressed DAG of Nodes. Every mutator returns a NEW State."""
    nodes: tuple = ()

    def with_node(self, node: Node) -> "State":
        merged = {n.id: n for n in self.nodes}
        merged[node.id] = node
        ordered = tuple(sorted(merged.values(), key=lambda n: n.id))
        return State(nodes=ordered)

    def get(self, node_id: str) -> "Node | None":
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    @property
    def root(self) -> str:
        """Content hash of the whole state — changes iff any node's output changes."""
        return content_hash([[n.id, content_hash(n.output)] for n in self.nodes])


class Adapter(Protocol):
    """The provider/harness-agnostic seam — the ONLY way work enters the kernel.

    P0 injects a deterministic adapter; P1 injects a HAL provider adapter (Pool B).
    """
    def dispatch(self, task: Any) -> Any: ...


# --- P1 primitive contracts (declared now, implemented in P1; Protocols are complete). ---
class Verify(Protocol):
    """Adversarial check. MUST be monotone — may only tighten a verdict, never relax it."""
    def __call__(self, result: Any, criterion: Any) -> bool: ...


class Synthesize(Protocol):
    """Exogenous judge / consensus over N results. No model grades its own homework."""
    def __call__(self, results: list) -> Any: ...


class Recurse(Protocol):
    """Re-dispatch a refined task until a criterion is met or a depth cap is hit."""
    def __call__(self, task: Any, refined: Any) -> Any: ...


@dataclass
class Result:
    output: Any
    state: State
    calls: int        # real adapter calls made THIS run
    cache_hits: int   # short-circuits (idempotency wins)


def run(task: Any, state: State, adapter: Adapter, cache: dict) -> Result:
    """Idempotent single-step execution.

    `content_hash([task, state.root])` keys the cache. An identical `(task, state)`
    returns the cached output with **zero** new adapter calls — the P0 idempotency
    guarantee (hypothesis H-UC-IDEM).
    """
    key = content_hash([task, state.root])
    if key in cache:
        # f∘f=f on the STATE projection too: a cache hit must yield the SAME
        # state as the miss branch (state.with_node), else threading run().state
        # across steps re-keys a replayed step and forces a new adapter call.
        # with_node dedups by node.id, so this is a no-op if already present.
        cached_node = Node(id=content_hash(task), input=task, output=cache[key])
        return Result(output=cache[key], state=state.with_node(cached_node), calls=0, cache_hits=1)
    output = adapter.dispatch(task)          # the ONLY place a real call happens
    cache[key] = output
    node = Node(id=content_hash(task), input=task, output=output)
    return Result(output=output, state=state.with_node(node), calls=1, cache_hits=0)
