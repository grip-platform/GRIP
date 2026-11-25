"""Broly council over the ultracode kernel — GRIP consumer #1 of lib/ultracode (H769).

``evaluate_criterion_mesh_idempotent()`` wraps ``lib.broly_council.evaluate_criterion_mesh``
with the kernel's content-addressed idempotency. The mesh / devil's-veto / verdict logic
is untouched — the kernel is injected underneath through the ``role_dispatch`` seam the
mesh already exposes.

What the kernel adds:

- **RESUME** — a council that died mid-run (crash, transient 429) resumes free: completed
  role legs replay from the persisted per-run cache with ZERO new provider calls; only
  the missing legs dispatch.
- **FRESH-BY-DEFAULT** — a COMPLETED run is never silently re-served. Asking the same
  question again samples fresh voices unless ``replay=True`` is passed explicitly. This
  default is the stale-cache guard hypothesis H769 measures (target: 0 incidents).
- **Monotone floor** — the final verdict passes a deterministic shape check via the
  kernel's ``verify`` primitive before it is served; a malformed council result raises.

Pool discipline: the live adapter routes through ``lib.broly_council.dispatch_role`` →
``_query_grip_infer`` (HAL Pool B; CCH banned per the broly billing mandate). The kernel
changes how often tokens are spent (replay, resume), never WHERE they are spent
(``rules/pool-a-offload-routing.md``).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from lib.broly_council import (DEFAULT_ROLE_ASSIGNMENTS, BrolyRoleAssignment,
                               dispatch_role, evaluate_criterion_mesh)
from lib.ultracode.kernel import State, content_hash, run
from lib.ultracode.primitives import verify

DEFAULT_RUN_DIR = Path(os.environ.get(
    "GRIP_BROLY_ULTRACODE_RUNS",
    str(Path.home() / ".claude" / "state" / "broly" / "ultracode-runs"),
))
TELEMETRY_PATH = Path(os.environ.get(
    "GRIP_ULTRACODE_TELEMETRY",
    str(Path.home() / ".claude" / "logs" / "ultracode-consumers.jsonl"),
))

_VALID_VERDICTS = ("met", "unmet", "inconclusive")


class GripInferAdapter:
    """ultracode ``Adapter`` over the council's live dispatch path (HAL Pool B)."""

    def __init__(self, assignments: list):
        self.assignments = assignments
        self.calls = 0

    def dispatch(self, task: dict) -> dict:
        self.calls += 1
        return dispatch_role(task["role"], task["prompt"], self.assignments)


def _run_key(claim: str, state: dict, assignments: list) -> str:
    fingerprint = [claim, state,
                   [[a.role, a.model, a.weight] for a in assignments]]
    return content_hash(fingerprint)[:16]


def _load_run(path: Path) -> dict:
    """Fail-safe load: a missing or corrupt run file means a fresh run, never garbage."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"cache": {}, "completed": False, "result": None}


def _telemetry(row: dict) -> None:
    """Append-only H769 telemetry; bookkeeping must never break a council."""
    try:
        TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TELEMETRY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    except OSError:
        pass


def verdict_shape_floor(result: dict, assignments: list) -> bool:
    """Deterministic l1 floor: a malformed council result must never be served."""
    if result.get("verdict") not in _VALID_VERDICTS:
        return False
    score = result.get("weighted_met_score", -1.0)
    if not isinstance(score, (int, float)) or not 0.0 <= score <= 1.0:
        return False
    voted_roles = {r.get("role") for r in result.get("per_role", [])}
    return voted_roles == {a.role for a in assignments}


def evaluate_criterion_mesh_idempotent(
    claim: str,
    state: dict,
    assignments: "Optional[list[BrolyRoleAssignment]]" = None,
    *,
    replay: bool = False,
    adapter=None,
    run_dir: "Optional[Path]" = None,
) -> dict:
    """Kernel-backed criterion mesh: resumable, fresh-by-default, telemetered.

    Semantics by run-file state for the same ``(claim, state, assignments)``:

    - incomplete run file → RESUME (completed legs replay from cache; only the
      missing legs make provider calls);
    - completed run file, ``replay=False`` (default) → FRESH sampling;
    - completed run file, ``replay=True`` → the recorded result returns as-is,
      zero provider calls.

    Returns the ``evaluate_criterion_mesh`` result dict plus an ``_ultracode``
    key: ``{run_key, resumed, replay, calls, cache_hits}``.
    """
    assignments = list(assignments or DEFAULT_ROLE_ASSIGNMENTS)
    adapter = adapter if adapter is not None else GripInferAdapter(assignments)
    base_dir = Path(run_dir) if run_dir else DEFAULT_RUN_DIR
    key = _run_key(claim, state, assignments)
    run_path = base_dir / f"{key}.json"
    record = _load_run(run_path)

    if record["completed"] and replay:
        result = dict(record["result"])
        result["_ultracode"] = {**result.get("_ultracode", {}),
                                "replay": True, "calls": 0, "cache_hits": 0}
        _telemetry({"ts": time.time(), "consumer": "broly_council",
                    "run_key": key, "resumed": False, "replay": True,
                    "calls": 0, "cache_hits": 0,
                    "verdict": result.get("verdict")})
        return result

    cache: dict = dict(record["cache"]) if not record["completed"] else {}
    resumed = bool(cache)
    counters = {"calls": 0, "cache_hits": 0}

    def _persist(completed: bool, result: "Optional[dict]" = None) -> None:
        base_dir.mkdir(parents=True, exist_ok=True)
        run_path.write_text(json.dumps(
            {"cache": cache, "completed": completed, "result": result},
            sort_keys=True, default=str), encoding="utf-8")

    def _kernel_dispatch(role: str, prompt: str, _assignments: list) -> dict:
        res = run({"role": role, "prompt": prompt}, State(), adapter, cache)
        counters["calls"] += res.calls
        counters["cache_hits"] += res.cache_hits
        _persist(completed=False)  # crash-safe checkpoint after every leg
        return res.output

    result = evaluate_criterion_mesh(claim, state, assignments,
                                     role_dispatch=_kernel_dispatch)
    if not verify(result, assignments, verdict_shape_floor):
        raise RuntimeError(
            f"broly council result failed the deterministic shape floor "
            f"(run {key}) — refusing to serve a malformed verdict")
    result["_ultracode"] = {"run_key": key, "resumed": resumed,
                            "replay": False, **counters}
    _persist(completed=True, result=result)
    _telemetry({"ts": time.time(), "consumer": "broly_council", "run_key": key,
                "resumed": resumed, "replay": False, **counters,
                "verdict": result["verdict"]})
    return result
