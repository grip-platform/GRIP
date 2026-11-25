#!/usr/bin/env python3
"""ULTRACLEAR — zero-loss three-way context partition (the WHAT-to-keep planner).

`/compact` re-derives a LOSSY summary by REPROCESSING the whole context window
(a full read of every token, ~0.10x base each — see cost_model below). ULTRACLEAR
does the opposite: it PARTITIONS the session and OFFLOADS rather than summarises,
so a fresh interactive window carries ZERO information loss BY CONSTRUCTION.

Three-way partition (every turn lands in exactly one bucket):

  HOT    — kept verbatim in a small bounded reload manifest: the recent `keep_recent`
           turns + any turn flagged `pinned` (load-bearing). Reloaded as ONE cache
           write of the manifest.
  COLD   — offloaded to a local ring store (lib/cold_context_pager.py — the
           WHAT-to-move mechanism this planner sits ABOVE). ~0 LLM tokens at rest;
           demand-paged back LOSSLESSLY only if a later turn references it.
  FOLDED — turns whose content is PROVABLY already captured in the holistic kernel
           (`in_kernel=True`). Not reloaded, no loss — the kernel already carries it.

Why offload beats summarise — the economics this planner inlines from
prompt-cache-keepalive's cost_model (Anthropic multipliers, 2026-05):

    cache write : 1.25x base   (re-establish the small manifest, once)
    cache read  : 0.10x base   (what /compact pays PER TOKEN to reprocess the window)

A 575k-token window reprocessed by `/compact` costs ~575k * 0.10 = 57,500
base-equivalents; reloading a 20k manifest costs ~20k * 1.25 = 25,000 — and the
manifest is LOSSLESS where the summary is not. The 12.5x read-vs-write gap
(0.10 vs 1.25, i.e. write costs 12.5x a read) is WHY a small lossless manifest
beats a full-window lossy reprocess: you pay one write of a few turns instead of
a read of the whole history, and you lose nothing.

COLD page-back is LAZY: it bills nothing until a turn is actually referenced, so it
is NOT in `reload_cost`. `is_noop` is True when the window is small enough that the
manifest reload is not cheaper than a compact reprocess — ultraclear is not worth it
there (small window ⇒ stay as-is).

DIP: a cold store may be injected to physically move COLD turns, but planning is
PURE — it never imports or requires one. `plan_ultraclear` decides the partition;
`lib/cold_context_pager.py::ColdContextPager` is the optional substrate that
executes the COLD move (same DIP / fail-closed-scope / lossless-round-trip ethos).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# NOTE (AGORA #3410 r3): this planner is PURE — it does NOT import the optional COLD
# substrate (`lib/cold_context_pager.py::ColdContextPager`). A caller that wants to
# PHYSICALLY move COLD turns imports + injects that pager itself (see
# `lib/cold_compaction_arm.py` / `lib/cold_compaction_gate.py`, which do exactly
# that). A re-export here was unused everywhere and contradicted the purity claim
# above — removed rather than kept as an untested optional-import path.


@dataclass(frozen=True)
class Turn:
    """One conversation turn with a caller-supplied token estimate.

    `pinned` marks a load-bearing turn (current task, an operator directive, a
    flagged decision) — it stays HOT and is NEVER demoted to COLD. `in_kernel`
    marks a turn whose content is already captured in the holistic kernel — it
    FOLDS (not reloaded, not lost).
    """

    id: str
    role: str
    content: str
    tokens: int
    pinned: bool = False
    in_kernel: bool = False


@dataclass(frozen=True)
class UltraclearConfig:
    """Tunables. Multipliers inlined from prompt-cache-keepalive cost_model
    (Anthropic 2026-05) to keep this module dependency-free."""

    keep_recent: int = 8
    manifest_target_tokens: int = 20_000
    cache_write_mult: float = 1.25
    cache_read_mult: float = 0.10


@dataclass(frozen=True)
class Plan:
    """The computed partition + its falsifiable economics."""

    hot: list[Turn] = field(default_factory=list)
    cold: list[Turn] = field(default_factory=list)
    folded: list[Turn] = field(default_factory=list)
    # The INPUT turn sequence (ids in original order), recorded at plan time so
    # `reconstruct_ordered` can restore exact original order UNCONDITIONALLY —
    # independent of bucket order or pinning (AGORA #3410 r3).
    original_order: list[str] = field(default_factory=list)
    manifest_tokens: int = 0
    original_tokens: int = 0
    reload_cost: float = 0.0
    compact_cost: float = 0.0
    net_savings: float = 0.0
    is_noop: bool = False
    manifest_exceeds_target: bool = False


def _is_recent(index: int, total: int, keep_recent: int) -> bool:
    """True iff `index` is within the last `keep_recent` positions of `total`."""
    return index >= total - keep_recent


def _classify_hot_cold(
    turns: list[Turn], config: UltraclearConfig
) -> tuple[list[Turn], list[Turn]]:
    """Split the NON-folded turns into HOT (recent OR pinned) and COLD (the rest),
    preserving original order in each bucket."""
    total = len(turns)
    hot: list[Turn] = []
    cold: list[Turn] = []
    for i, t in enumerate(turns):
        if t.pinned or _is_recent(i, total, config.keep_recent):
            hot.append(t)
        else:
            cold.append(t)
    return hot, cold


def _demote_until_under_target(
    hot: list[Turn], cold: list[Turn], kernel_tokens: int, target: int
) -> tuple[list[Turn], list[Turn], int]:
    """Demote the OLDEST non-pinned HOT turns to COLD until the manifest
    (sum(hot)+kernel) is <= target. Pinned turns are load-bearing and never move,
    so the floor is `pinned+kernel`: if that alone exceeds target, stop there and
    expose the irreducible floor rather than silently violate it.

    Returns the rebalanced (hot, cold, manifest_tokens). COLD demotions are
    PREPENDED in original-position order so `cold` stays oldest-first.
    """
    hot = list(hot)
    demoted: list[Turn] = []
    manifest = sum(t.tokens for t in hot) + kernel_tokens
    # hot is in original order; walk oldest-first, skipping pinned.
    idx = 0
    while manifest > target and idx < len(hot):
        if hot[idx].pinned:
            idx += 1
            continue
        moved = hot.pop(idx)
        demoted.append(moved)
        manifest -= moved.tokens
        # do NOT advance idx: list shifted left under the popped slot.
    # GLOBAL oldest-first (AGORA #3410 r3): `cold` (from _classify_hot_cold) holds
    # the OLD turns BEFORE the recent window; `demoted` holds recent-window turns
    # just shed from HOT — every demoted turn is therefore strictly NEWER than every
    # `cold` turn. Both are already internally oldest-first, so the correct global
    # order is cold ++ demoted, NOT demoted ++ cold (the latter put the newer demoted
    # turns ahead of the older cold ones, reversing oldest-first when both are
    # non-empty — i.e. demotion co-occurring with scattered pins).
    return hot, list(cold) + demoted, manifest


def plan_ultraclear(
    turns: Iterable[Turn],
    kernel_tokens: int,
    config: UltraclearConfig = UltraclearConfig(),
) -> Plan:
    """Partition `turns` three ways with ZERO information loss, and compute whether
    the lossless reload beats a `/compact` reprocess.

    Gates (each pinned by a test in tests/test_ultraclear.py):
      1. ZERO-LOSS PARTITION — hot|cold|folded recovers ALL input ids, disjoint.
         FOLDED iff `in_kernel`; else HOT iff (recent OR pinned) else COLD.
      2. BOUNDED MANIFEST — manifest = sum(hot)+kernel; demote oldest non-pinned
         HOT to COLD until <= target (pinned never demoted; floor = pinned+kernel).
      3. ECON — reload_cost = manifest*write_mult; compact_cost =
         original*read_mult (compact REPROCESSES the whole window); net_savings =
         compact_cost - reload_cost; is_noop when net_savings <= 0. COLD page-back
         is lazy/zero and is NOT in reload_cost.
    """
    turns = list(turns)
    original_tokens = sum(t.tokens for t in turns)
    original_order = [t.id for t in turns]  # input sequence — drives reconstruct_ordered

    folded = [t for t in turns if t.in_kernel]
    live = [t for t in turns if not t.in_kernel]
    hot, cold = _classify_hot_cold(live, config)

    hot, cold, manifest_tokens = _demote_until_under_target(
        hot, cold, kernel_tokens, config.manifest_target_tokens
    )

    reload_cost = manifest_tokens * config.cache_write_mult
    compact_cost = original_tokens * config.cache_read_mult
    net_savings = compact_cost - reload_cost
    is_noop = net_savings <= 0

    # IRREDUCIBLE-FLOOR TRANSPARENCY (AGORA #3410 r2): True iff the manifest STILL
    # exceeds target after demotion — which can only happen because pinned turns +
    # kernel ALONE already exceed it (demotion never moves pinned). The floor is
    # surfaced, not silently violated. False ⇒ the manifest is genuinely <= target.
    manifest_exceeds_target = manifest_tokens > config.manifest_target_tokens

    return Plan(
        hot=hot,
        cold=cold,
        folded=folded,
        original_order=original_order,
        manifest_tokens=manifest_tokens,
        original_tokens=original_tokens,
        reload_cost=reload_cost,
        compact_cost=compact_cost,
        net_savings=net_savings,
        is_noop=is_noop,
        manifest_exceeds_target=manifest_exceeds_target,
    )


def reconstruct(plan: Plan, paged_back_cold_ids: Iterable[str] = ()) -> set[str]:
    """Return the set of turn ids RECOVERABLE from a plan: HOT ∪ FOLDED ∪ the
    requested COLD ids (lazily paged back).

    Losslessness invariant (pinned by a test): with EVERY cold id paged back, this
    equals the full original id set — nothing in the partition is unrecoverable.
    """
    requested = set(paged_back_cold_ids)
    # Fail LOUD on a stale/unknown page-back id (validation shared with
    # reconstruct_ordered — DRY): silently dropping it would under-recover the set
    # without anyone noticing (AGORA #3410).
    _validate_cold_request(plan, requested)
    recovered = {t.id for t in plan.hot} | {t.id for t in plan.folded}
    recovered |= requested  # guaranteed ⊆ cold_ids now
    return recovered


def _validate_cold_request(plan: Plan, requested: set[str]) -> set[str]:
    """Shared validation for the reconstruct family: reject any requested id this
    plan never offloaded. Returns the COLD id set on success; raises ValueError
    LOUD on any unknown id (a stale/unknown page-back is a caller bug, not a no-op —
    AGORA #3410). DRY: both `reconstruct` and `reconstruct_ordered` rely on it."""
    cold_ids = {t.id for t in plan.cold}
    unknown = requested - cold_ids
    if unknown:
        raise ValueError(
            f"reconstruct: {len(unknown)} requested id(s) are not COLD in this plan "
            f"(stale/unknown): {sorted(unknown)[:5]}"
        )
    return cold_ids


def reconstruct_ordered(
    plan: Plan, paged_back_cold_ids: Iterable[str] = ()
) -> list[str]:
    """Like `reconstruct`, but returns the recoverable ids as a LIST in ORIGINAL
    TURN ORDER instead of an order-free set — SEQUENCE losslessness, not just SET
    losslessness (AGORA #3410).

    Order is driven by `plan.original_order` (the input turn sequence recorded at
    plan time), so the result is the recoverable ids in their EXACT original
    positions — UNCONDITIONALLY, for ANY partition shape. This is the r3 fix for
    AGORA #3410: the earlier FOLDED → COLD → HOT bucket-concatenation only
    reproduced original order when the buckets were chronologically contiguous, a
    precondition that `pinned` turns break (a pinned OLD turn lands in HOT yet is
    chronologically early) and that demotion-rebalanced COLD also broke. Ordering by
    recorded original position removes the precondition entirely.

    Recoverable = HOT ∪ FOLDED ∪ the COLD ids actually paged back (HOT and FOLDED are
    always recoverable; COLD pages back lazily by id). With EVERY cold id paged back,
    the result is the full original id list in input order — nothing is unrecoverable
    AND nothing is re-sequenced. Raises the same ValueError as `reconstruct` on an
    unknown/stale requested COLD id (validation reused — DRY).
    """
    requested = set(paged_back_cold_ids)
    _validate_cold_request(plan, requested)
    recoverable = {t.id for t in plan.hot} | {t.id for t in plan.folded} | requested
    return [tid for tid in plan.original_order if tid in recoverable]
