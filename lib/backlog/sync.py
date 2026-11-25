"""W2 — backlog sync shim (the syscall): idempotent, IDR-notarised, W0-gated.

Plan unified-backlog-issue-system.md L2. CBC invariants enforced *by
construction*:

  2. IDR is integrity, NEVER LLM-augmented — the idr_ref is built by
     `relay_decision.build_idr_ref` (deterministic sha256 chain, the
     canonical happi/1.1 primitive from HAL #328; `model_versions=[]`
     proves no model touched it). llm-augmentation-pattern denylist:
     explicit.
  3. Field-partitioned ownership + content-hash write-gate — sync only
     ever emits a fenced provenance block (machine fields); it never
     writes human-owned prose (title/body/labels). A mutation is skipped
     unless the rendered block's content hash changed (identical substrate
     ⇒ 0 writes ⇒ no write-storm).

Binding-DA invariant: the system is READ-ONLY until the full triad is
validated (W5 dogfood). `sync(..., execute=False)` is the DEFAULT and
performs ZERO external writes. Even `execute=True` routes every write
through the merged W0 anti-blast TRIAD (`antiblast.check`) first and uses
an injected backend (default = NoOpBackend) — Rule 21: compose the W0
gate, never reimplement it.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, Optional, Protocol, Sequence

from lib.backlog.antiblast import check  # W0 TRIAD (compose, non-bypassable)
from lib.backlog.model import BacklogItem
from lib.relay_decision import build_idr_ref  # canonical mechanical IDR

_CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
_SYNC_LOG = _CLAUDE_DIR / "logs" / "backlog-sync.jsonl"

_FENCE_OPEN = "<!-- grip-backlog:provenance v1 -->"
_FENCE_CLOSE = "<!-- /grip-backlog:provenance -->"


def render_provenance_block(item: BacklogItem) -> str:
    """Deterministic fenced MACHINE block — never human prose.

    Only machine-owned fields go here; title/body are human-owned and are
    NEVER written by sync (the field partition is structural: the backend
    contract receives only this block, so it physically cannot clobber
    human fields).
    """
    payload = {
        "id": item.id,
        "source": item.source,
        "repo": item.repo,
        "priority": item.priority,
        "status": item.status,
        "provenance": dict(sorted(item.provenance.items())),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return f"{_FENCE_OPEN}\n{body}\n{_FENCE_CLOSE}"


def content_hash(rendered: str) -> str:
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


class Backend(Protocol):
    """Hot-swappable sink (GitHub Issues / Projects v2 / Pages) — W3+ surfaces."""

    def upsert(self, item: BacklogItem, provenance_block: str) -> str: ...


class NoOpBackend:
    """Default sink: performs no external write. The READ-ONLY-until-W5 floor."""

    def upsert(self, item: BacklogItem, provenance_block: str) -> str:  # noqa: ARG002
        return "noop"


@dataclass(frozen=True, slots=True)
class MutationPlan:
    item_id: str
    op: str            # "upsert" | "skip"
    provenance_block: str
    content_hash: str
    idr_ref: dict
    executed: bool = False


def plan_mutation(item: BacklogItem, prior_hash: Optional[str] = None) -> MutationPlan:
    """Pure planning: render → hash-gate → mechanical IDR. No I/O except IDR chain read."""
    block = render_provenance_block(item)
    h = content_hash(block)
    op = "skip" if prior_hash == h else "upsert"  # content-hash write-gate
    envelope_raw = json.dumps(
        {"id": item.id, "op": op, "hash": h}, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    completed = json.dumps(
        {"type": "completed", "op": op, "hash": h}, separators=(",", ":")
    )
    idr_ref = build_idr_ref(
        f"backlog:{item.id}", envelope_raw, completed, telemetry_path=_SYNC_LOG
    )
    return MutationPlan(item.id, op, block, h, idr_ref)


def _append_idr(plan: MutationPlan) -> None:
    """Notarise the mutation decision (GRIP-side mirror, #2720 forward-compat).

    Loud-not-silent (feedback_fix_silent_failures_loudly): a lost IDR-mirror
    write is a lost notarisation — it screams on stderr but never breaks the
    sync (an audit-write failure must not suppress the decision)."""
    try:
        _SYNC_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _SYNC_LOG.open("a") as fh:
            fh.write(json.dumps({"plan": plan.item_id, "op": plan.op,
                                 "idr_ref": plan.idr_ref}) + "\n")
    except OSError as exc:
        # Narrow to OSError — the ONLY expected failure of a file append
        # (disk full / permission / missing dir). Loud-not-silent for that.
        # A non-OSError here is a PROGRAMMING bug (e.g. a json.dumps typo)
        # and MUST propagate, not be masked as a benign "audit-write
        # failure" — masking a real bug in the IDR path is exactly the
        # feedback_fix_silent_failures_loudly anti-pattern.
        import sys
        print(f"[backlog-sync] IDR MIRROR WRITE FAILED for {plan.item_id} "
              f"({exc!r}) — mutation decision UNNOTARISED at {_SYNC_LOG}",
              file=sys.stderr)


def sync(
    model: Sequence[BacklogItem],
    *,
    execute: bool = False,
    backend: Optional[Backend] = None,
    prior_hashes: Optional[Mapping[str, str]] = None,
) -> list[MutationPlan]:
    """Idempotent, IDR-notarised reconcile. READ-ONLY unless execute=True.

    Every item ⇒ a notarised MutationPlan. Only changed (hash-gated) items
    are write-candidates; only with execute=True AND a backend AND passing
    the W0 TRIAD are they actually written.
    """
    backend = backend or NoOpBackend()
    prior = dict(prior_hashes or {})
    plans: list[MutationPlan] = []
    for item in model:
        plan = plan_mutation(item, prior.get(item.id))
        _append_idr(plan)  # notarise the DECISION always (skip or upsert)
        if plan.op == "upsert" and execute:
            # W0 anti-blast TRIAD BEFORE any external write — non-bypassable.
            # A non-authorised repo raises DenyError out of this call.
            check(
                repos=[item.repo] if item.repo else [],
                paths=[],
                mutation_count=1,
            )
            backend.upsert(item, plan.provenance_block)
            plan = replace(plan, executed=True)
        plans.append(plan)
    return plans
