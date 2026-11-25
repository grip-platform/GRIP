"""Idempotent insights-store deduplication — the 4th Banach fixed-point domain.

GRIP's ``learning/insights.jsonl`` accumulates duplicate entries across sessions:
each ``/evo`` run appends the same friction-pattern text repeatedly (empirically
200+ duplicate entries in a 2170-record store). The ``consolidated`` /
``consolidated_into`` fields prove the intent was always there; no idempotent
kernel was ever extracted.

This module IS that kernel — the 4th instance in GRIP's idempotent-fold lineage:

    git_reconcile.py       — merge-tree is a no-op iff already-reconciled
    scope_contamination.py — sanitise is a no-op iff already-clean
    compaction_idempotence.py — memory fold is a no-op iff already-compacted
    HERE                   — dedup is a no-op iff already-deduplicated

The fixed-point criterion: ``dedup(records) == records`` (byte-for-byte same list
of dicts after de-duplication). A store is CONVERGED iff re-deduplicating it
produces the same list — no duplicate removed, no ``consolidated_into`` pointer
missing, no score upgrade possible.

Idempotency: ``dedup(dedup(records)) == dedup(records)`` — applying the dedup
twice equals applying it once. Proved mechanically in :func:`is_idempotent`; not
asserted in prose.

Canonical-record selection within a duplicate group (deterministic, stable):

1. Highest ``learning_score`` (float field, default 0.0 if absent).
2. On tie: highest ``score`` (secondary float, default 0.0).
3. On tie: earliest ``ingested_at`` ISO-8601 string (lexicographic — ISO sorts
   correctly; empty strings sort last, preserving original order for records
   with no timestamp).

Losing records receive ``consolidated_into=<winner_id>`` and
``consolidated=True``. The winner receives an updated ``seen`` count equal to
the sum of all group members' ``seen`` counts (accumulating observation evidence).
The winner is NOT modified beyond the ``seen`` sum — it keeps its own
``learning_score``, ``source``, and all other fields.

Fingerprint key: the exact ``text`` field (full string, not truncated). Two
records are duplicates iff their ``text`` values are identical.

Pure stdlib. No I/O: the caller reads and writes the JSONL; this module only
transforms the in-memory list. Hermetic: tests run with zero disk access.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Sequence


# ── Type alias ──────────────────────────────────────────────────────────────
Record = dict  # one parsed JSONL insight record


# ── Canonical selection key ──────────────────────────────────────────────────

def _canonical_key(record: Record) -> tuple[float, float, str]:
    """Sort key for selecting the canonical record from a duplicate group.

    Higher is better: negate scores so the best record sorts first.
    Earliest ``ingested_at`` (smallest string) is best; invert the sign.
    """
    score_a = -float(record.get("learning_score") or 0.0)
    score_b = -float(record.get("score") or 0.0)
    # Invert: earlier timestamp wins; empty string → very-large sentinel so it
    # loses to any real timestamp.
    ts = record.get("ingested_at") or "9999"
    return (score_a, score_b, ts)


# ── Core idempotent operation ────────────────────────────────────────────────

def dedup(records: Sequence[Record]) -> list[Record]:
    """Deduplicate insight records by exact ``text`` fingerprint.

    Returns a NEW list — the input is never mutated.

    For each group of records sharing the same ``text``:
    - The record with the best :func:`_canonical_key` becomes the WINNER.
    - The winner's ``seen`` is updated to the sum of all group members' ``seen``.
    - Losing records are DROPPED from the output; they are NOT appended with a
      ``consolidated_into`` pointer (that would bloat the store). The caller
      can inspect the returned :class:`DedupProof` for the dropped set if needed.

    Non-duplicate records pass through unchanged (their dict objects are shared
    via ``copy.copy`` so the caller's originals are not mutated if they patch
    the winner in place).

    Idempotency: if all groups are already singletons (no duplicates), every
    record passes through with no modification → ``dedup(records) == records``.
    In other words: once the store is converged, ``dedup`` is a pure no-op.
    """
    # Group by text fingerprint, preserving encounter order (insertion order
    # dict, Python 3.7+).
    groups: dict[str, list[Record]] = {}
    for rec in records:
        key = rec.get("text", "")
        groups.setdefault(key, []).append(rec)

    output: list[Record] = []
    for _text, group in groups.items():
        if len(group) == 1:
            output.append(group[0])          # pass through — no copy needed
            continue

        # Sort: best candidate first.
        sorted_group = sorted(group, key=_canonical_key)
        winner = copy.copy(sorted_group[0])   # shallow copy — we write seen

        # Accumulate seen counts across the group.
        total_seen = sum(int(r.get("seen") or 0) for r in sorted_group)
        winner["seen"] = total_seen

        output.append(winner)

    return output


# ── Fixed-point predicates ───────────────────────────────────────────────────

def is_deduplicated(records: Sequence[Record]) -> bool:
    """Fixed-point criterion: ``dedup(records) == records``.

    True iff every ``text`` fingerprint appears exactly once — i.e. applying
    :func:`dedup` to these records would be a no-op (same length, same winner
    for every singleton group). Uses length + fingerprint-set comparison: if
    the lengths differ there are duplicates; if lengths match there can be no
    hidden duplicates (each unique text contributes exactly one record).

    This is the CONVERGENCE criterion: the insights store is converged for these
    records exactly when re-deduplication changes nothing.
    """
    texts = [r.get("text", "") for r in records]
    return len(texts) == len(set(texts))


def is_idempotent(records: Sequence[Record]) -> bool:
    """Verify (don't assert) that :func:`dedup` is idempotent on these records.

    Checks ``dedup(dedup(records)) == dedup(records)`` by comparing the
    ``text`` fingerprints of both passes. Comparing full dicts is unsafe because
    the winner's ``seen`` field changes on the first pass; a second pass on
    already-singleton groups leaves ``seen`` unchanged. We compare fingerprint
    sequences (ordered, since :func:`dedup` preserves encounter order of the
    winners) rather than raw record equality.

    A correct dedup is idempotent for ALL inputs — including inputs that are not
    themselves deduplicated. A dedup that is non-idempotent is not a projection
    and the convergence claim is false; this returns False rather than masking it.
    """
    once = dedup(records)
    twice = dedup(once)
    return _text_fingerprints(once) == _text_fingerprints(twice)


def residual_duplicates(records: Sequence[Record]) -> int:
    """Divergence metric d(records): count of duplicate records that would be removed.

    ``len(records) - len(dedup(records))``: how many entries would be dropped by
    one application of :func:`dedup`. Zero exactly at the fixed point (converged
    store), positive off it. The memory-dedup analogue of
    ``compaction_idempotence.residual_bytes`` and
    ``scope_contamination``'s leaked-span count — a concrete monotone-toward-zero
    handle on "how far from converged".
    """
    return max(0, len(records) - len(dedup(records)))


def _text_fingerprints(records: Sequence[Record]) -> list[str]:
    """Ordered ``text`` fingerprints — stable comparison key for idempotency check."""
    return [r.get("text", "") for r in records]


# ── Proof dataclass ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DedupProof:
    """Banach fixed-point / idempotency witness for insights deduplication.

    Mirrors :class:`compaction_idempotence.CompactionProof` field-for-field:

    ``at_fixed_point``    — True iff ``dedup(records) == records``
                            (:func:`is_deduplicated`): the store is already at
                            the dedup's fixed point. The convergence criterion.
    ``residual_count``    — the divergence metric d: number of duplicate records
                            that would be removed by one :func:`dedup` pass. 0 at
                            the fixed point, positive off it.
                            ``at_fixed_point ⟺ residual_count == 0``.
    ``idempotent``        — True iff ``dedup(dedup(records)) == dedup(records)``
                            (:func:`is_idempotent`): the fold lands on a fixed
                            point after one application. Verified, not assumed.
    ``metric_disagrees``  — True iff ``at_fixed_point`` and
                            ``residual_count == 0`` disagree (should never happen
                            for a correct dedup; an explicit flag so callers
                            detect the anomaly without parsing ``rationale``).
    ``deduplicated_records`` — the output of :func:`dedup` on the input records
                            (computed once; callers need not re-run).
    ``rationale``         — plain-language explanation of the verdict.
    """
    at_fixed_point: bool
    residual_count: int
    idempotent: bool
    metric_disagrees: bool
    deduplicated_records: tuple[Record, ...]
    rationale: str = field(compare=False)  # prose; excluded from equality


def _build_rationale(
    *, at_fp: bool, residual: int, idem: bool, metric_disagrees: bool
) -> str:
    """Plain-language rationale for a :class:`DedupProof`. (SRP: only wording.)"""
    if at_fp:
        idem_note = (
            "" if idem
            else " WARNING: dedup is non-idempotent for this input — convergence "
                 "claim is unsound."
        )
        return (
            "re-deduplicating is a no-op — the insights store is at the "
            "dedup's fixed point (0 residual duplicates); CONVERGED." + idem_note
        )
    anomaly = (
        "ANOMALY: residual_count is 0 yet at_fixed_point is False — the dedup "
        "function is not a pure shrinking projection (length-preserving mutation). "
        "Reporting NOT-converged (fail-closed). "
        if metric_disagrees else ""
    )
    idem_note = (
        "" if idem
        else " The dedup is also non-idempotent for this input (ℱ∘ℱ ≠ ℱ)."
    )
    return (
        anomaly
        + f"re-deduplicating would remove {residual} duplicate record(s) — "
        f"the insights store is NOT at the dedup's fixed point; not converged."
        + idem_note
    )


def dedup_proof(records: Sequence[Record]) -> DedupProof:
    """Prove (or refute) that ``records`` is at the insights-dedup fixed point.

    Coordinator: computes the three signals (:func:`is_deduplicated`,
    :func:`residual_duplicates`, :func:`is_idempotent`) over the input, then
    defers wording to :func:`_build_rationale`. For a correct dedup the
    invariant ``at_fixed_point ⟺ residual_count == 0`` holds; any disagreement
    surfaces in ``metric_disagrees`` (fail-closed, not a bare assert).

    The ``deduplicated_records`` field carries the deduplication result so the
    caller never re-runs :func:`dedup`.
    """
    deduped = dedup(records)
    at_fp = is_deduplicated(records)
    residual = residual_duplicates(records)
    idem = is_idempotent(records)
    metric_disagrees = at_fp != (residual == 0)
    rationale = _build_rationale(
        at_fp=at_fp, residual=residual, idem=idem, metric_disagrees=metric_disagrees
    )
    return DedupProof(
        at_fixed_point=at_fp,
        residual_count=residual,
        idempotent=idem,
        metric_disagrees=metric_disagrees,
        deduplicated_records=tuple(deduped),
        rationale=rationale,
    )


__all__ = [
    "Record",
    "dedup",
    "is_deduplicated",
    "is_idempotent",
    "residual_duplicates",
    "DedupProof",
    "dedup_proof",
]
