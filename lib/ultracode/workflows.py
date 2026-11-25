"""ultracode workflows — the named-workflow registry the harness seam dispatches by.

A HAPPI envelope is pure data (polyglot: any harness, any language, any process can
produce one), so the injected pure functions a topology needs (step / expand / score /
judge) cannot travel inside it. The envelope therefore NAMES a workflow, and every
harness resolves the same name from this registry — the same move HAPPI itself makes
for `cmd` dispatch. Adding a workflow = one registry entry; the harness seam is closed.

Every function here is pure and deterministic, so a workflow produces the identical
content-addressed Result in any process on any machine — the property the P3
cross-harness proof (H-UC-HARNESS) measures.
"""
from __future__ import annotations

from typing import Any

from .kernel import content_hash


def _hash_score(output: Any) -> float:
    """Deterministic pseudo-score in [0, 1] derived from the output's content hash."""
    return int(content_hash(output)[:8], 16) / 0xFFFFFFFF


def _majority_judge(results: list) -> Any:
    """Exogenous consensus: most frequent result wins; ties break by content hash."""
    ranked = sorted(results, key=lambda r: (-results.count(r), content_hash(r)))
    return ranked[0]


# --- echo_chain: the minimal CoT workflow the harness tests anchor on ---

def _echo_reason(task: Any) -> Any:
    return {"echo": task, "h": content_hash(task)[:12]}


def _echo_step(output: Any, i: int) -> Any:
    return {"_from": output["h"], "_step": i + 1}


# --- teach_quiz: this repo's own session workflow (the P4 dogfood workload) ---

TEACH_STAGES = ("teach", "build-deck", "quiz", "council-verify")


def _teach_reason(task: Any) -> Any:
    stage = task.get("_stage", TEACH_STAGES[0]) if isinstance(task, dict) else TEACH_STAGES[0]
    return {"stage": stage, "artifact": content_hash(task)[:12]}


def _teach_step(output: Any, i: int) -> Any:
    nxt = TEACH_STAGES[min(i + 1, len(TEACH_STAGES) - 1)]
    prev = output["artifact"] if isinstance(output, dict) else content_hash(output)[:12]
    return {"_stage": nxt, "_prev": prev}


# --- forest_vote: FoT coverage through the seam (2 trees + exogenous judge) ---

def _pair_expand(task: Any) -> list:
    return [{"_branch": 0, "_of": content_hash(task)[:8]},
            {"_branch": 1, "_of": content_hash(task)[:8]}]


WORKFLOWS: dict[str, dict] = {
    "echo_chain": {
        "reason": _echo_reason,
        "cfg": lambda: {"topology": "chain", "step": _echo_step, "n_steps": 3},
    },
    "teach_quiz": {
        "reason": _teach_reason,
        "cfg": lambda: {"topology": "chain", "step": _teach_step,
                        "n_steps": len(TEACH_STAGES)},
    },
    "forest_vote": {
        "reason": _echo_reason,
        "cfg": lambda: {"topology": "forest", "expand": _pair_expand,
                        "score": _hash_score, "judge": _majority_judge,
                        "n_trees": 2, "beam": 1, "depth": 1},
    },
}
