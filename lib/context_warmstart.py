"""Context-chain warm-start resolver (P6 — bootstrap-gated, human-merge only).

The READ side of the signed context memory-chain: resolve the warm-start
``next_up`` dict from the chain's latest VERIFIED node instead of the legacy
``next_step_block.read_latest_next_up`` (which reads the unsigned instance
index). Side-B (writing a signed ``context-delta`` carrying ``next_step`` by
value on every /save) already shipped in P5 — ``grip_save._save_context_chain``.

THE SAFETY CONTRACT (why this is safe to merge before any chain exists):

  * Absent chain (no HEAD / empty file) ⇒ ``resolve_next_up`` returns ``None``
    so the caller falls through to the legacy read and produces BYTE-IDENTICAL
    output. Merging this changes NOTHING until a chain actually exists.
  * BROKEN chain ⇒ verify the chain ending at each candidate newest→oldest,
    each judged on a prefix of ONE read of the file (GRIP#6220); take the
    newest VERIFIED node carrying a non-empty
    ``decision.next_step``. A flipped byte drives ``verify_context_chain`` to
    ``BROKEN``, so a tampered leaf is skipped — we never read an unverified
    next_step. If a BROKEN head forces falling back to an OLDER verified node,
    emit a LOUD stderr line (monotone-safe; NEVER fabricate a next_step).
  * No VERIFIED node carries a next_step ⇒ ``None`` (legacy fallback).

This module is READ-ONLY over the chain and imports no HAL/LLM (the P3/P5
import-isolation discipline — verify path stays crypto-only).

Design: GRIP ``drafts/happi-context-event-memory-chain-design.md`` §4, §7 (P6).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def _is_nonempty_next_step(node) -> bool:
    """True iff the node's ``decision.next_step`` is a non-empty dict.

    A ``context-delta`` written by ``context_chain.checkpoint`` always carries a
    ``decision.next_step`` dict (empty ``{}`` when there was no next_up at save
    time). Only a non-empty one is a real warm-start signal; ``{}`` is treated as
    "no signal here" so we keep walking back to an older node that has one.
    """
    decision = getattr(node, "decision", None)
    if not isinstance(decision, dict):
        return False
    ns = decision.get("next_step")
    return isinstance(ns, dict) and bool(ns)


def _prefix_verdicts(chain, chain_path, verdict_of_chain, verify_context_chain):
    """Return a function that judges the chain ending at one candidate node.

    ``chain`` is the root-to-HEAD walk from ONE read of the file. For the node at
    index ``i`` of it, the walk that ``verify_context_chain(head=node.id)`` makes
    after re-reading the same file is ``chain[:i + 1]``: the same last-record-wins
    lookup, stopping at the same missing or empty predecessor. So the verdict is
    ``verdict_of_chain`` on that prefix, with no second read. The exception is a
    cycle: when the root's predecessor is itself in the walk, the reader stopped
    at an id it had already visited, and the walk from an inner node is not a
    prefix (GPT-6 Astra's review, 2026-11-25). Then every verdict comes from
    ``verify_context_chain`` as before, its file read included.

    The saving is the re-reads. Each prefix is still verified in full, so when the
    walk builds but every candidate fails, the verification work stays quadratic
    in the walk's length, as it was before this change.
    """
    index = {node.id: i for i, node in enumerate(chain)}
    try:
        cyclic = chain[0].predecessor_idr in index
    except TypeError:
        # An unhashable predecessor (an empty list) cannot name a record, and the
        # reader stopped on it, so it is no cycle: stay on the one read, where every
        # candidate reads BROKEN as before (GPT-6 Astra's R0 on GRIP#9913).
        cyclic = False
    if cyclic:
        return lambda node: verify_context_chain(path=chain_path, head=node.id)
    return lambda node: verdict_of_chain(chain[: index[node.id] + 1], chain_path)


def resolve_next_up(
    *,
    chain_path: Optional[Path] = None,
    head_pointer: Optional[Path] = None,
    warn=None,
) -> Optional[dict]:
    """Resolve the warm-start ``next_up`` from the latest VERIFIED chain node.

    Returns the ``decision.next_step`` dict of the newest chain node that BOTH
    carries a non-empty ``next_step`` AND whose chain (root→that node) verifies
    ``VERIFIED``. Returns ``None`` on: absent chain, no node with a next_step, or
    no such node that also verifies — in every ``None`` case the caller falls
    back to the legacy read and warm-start is byte-identical to today.

    ``warn`` is an injected sink (defaults to a stderr writer) called ONCE with a
    plain-language line iff a BROKEN/unverified newer node forced falling back to
    an OLDER verified node — so a corrupted head is loud, never silent, and we
    never fabricate a next_step. Pure-ish: only reads the chain + writes the one
    warn line; never raises into the launch (any error ⇒ ``None`` = legacy path).
    """
    if warn is None:
        def warn(msg: str) -> None:  # pragma: no cover — trivial default sink
            print(msg, file=sys.stderr)

    try:
        from lib.audit_chain import Verdict
        from lib.context_chain import (
            mask_superseded,
            read_context_chain,
            verdict_of_chain,
            verify_context_chain,
        )
    except ImportError:
        return None  # chain modules absent on a partial install → legacy path

    try:
        chain = read_context_chain(path=chain_path, head_pointer=head_pointer)
    except Exception:
        return None  # unreadable chain → legacy path (never crash the launch)
    if not chain:
        return None  # absent/empty chain → byte-identical legacy fallback

    # Walk newest→oldest over the chain AS A READER SEES IT: a checkpoint that a
    # later ``context-supersede`` names is masked, so a withdrawn plan is never
    # warm-started (MEMORY_CONTEXT.md §context-supersede). Verification below
    # still runs over the UNMASKED chain (root→node), so nothing is weakened.
    # The FIRST node that carries a next_step is the head's intent; if the chain
    # ending there is BROKEN we keep walking to an older one and warn that we
    # degraded (monotone-toward-safe).
    try:
        verdict_at = _prefix_verdicts(chain, chain_path, verdict_of_chain, verify_context_chain)
    except Exception:
        # A safety net, never the normal path: if building the judge fails for a
        # reason no test foresaw, judge each candidate as before, where any error
        # inside a verify reads as BROKEN, so the launch never crashes.
        def verdict_at(node):
            return verify_context_chain(path=chain_path, head=node.id)
    skipped_newer_broken = False
    for node in reversed(mask_superseded(chain)):
        if not _is_nonempty_next_step(node):
            continue
        try:
            verdict = verdict_at(node)
        except Exception:
            verdict = Verdict.BROKEN  # treat a verify error as unverified → skip
        if verdict is Verdict.VERIFIED:
            if skipped_newer_broken:
                warn(
                    "[GRIP WARM-START] Newest signed next-step did not verify — "
                    "fell back to an older VERIFIED checkpoint. Run `grip doctor` "
                    "to inspect the context chain (state/precog/context.jsonl)."
                )
            return dict(node.decision["next_step"])
        # This candidate did not verify; remember we degraded for the warn line.
        skipped_newer_broken = True

    return None  # no VERIFIED node carries a next_step → legacy fallback
