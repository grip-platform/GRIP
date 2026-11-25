"""Memory-compaction idempotence kernel — the holistic fold as a fixed point.

GRIP's memory compaction (the holistic fold in ``lib/holistic_synthesiser.py``)
already CLAIMS to be idempotent: its module docstring states ``ℱ(folded) =
folded`` and ``synthesise`` promises "same substrate ⇒ byte-identical text". This
kernel is the mechanical analogue of ``lib/git_reconcile.py`` (git divergence as
a Banach fixed point) and ``lib/scope_contamination.py`` (scope cleanliness as a
sanitise no-op), transplanted from those domains to the memory-compaction one:

    git-solve:   reconciled  iff merge-tree(other, branch) reproduces other's
                 tree — applying the merge is a NO-OP.
    scope:       clean       iff sanitise(content, target) == content —
                 stripping out-of-scope tokens is a NO-OP.
    here:        compacted   iff compact(content) == content —
                 re-compacting the kernel is a NO-OP.

So "compacted" is PROVEN, never asserted: ``compact(content) == content``. The
non-empty *delta* between the content and its compaction IS the residual the
fold has not yet folded — concrete bytes, not a vague "this feels uncompacted",
exactly as ``git merge-tree`` gives the conflict hunks for free and ``sanitise``
gives the leaked spans for free.

The thesis (the third /dogfood instance): **GRIP's memory compaction is
CONVERGED iff re-compacting is a no-op.** The holistic fold is the GRIP
convergence kernel applied to memory — it contracts the "uncompacted residual"
metric ``d(content) = residual_bytes`` toward the UNIQUE fixed point ``x*`` where
``d(x*) = 0``. At ``x*`` re-compaction changes nothing, which is simultaneously:

  (a) the CONVERGENCE criterion (the fold has reached its fixed point),
  (b) the BANACH uniqueness witness (an idempotent projection has exactly one
      image-fixed-point set; ``compact`` cannot converge anywhere else for this
      input), and
  (c) the IDEMPOTENCY proof (``compact(x*) = x*``; re-running changes nothing).

This module is the mechanical floor ONLY. It never reads disk, never writes,
never runs the real synthesiser — the compactor is INJECTED (DIP, hermetic), so
tests run with zero I/O. The *judgment* of what to do with a non-fixed-point
content (re-run the fold, investigate why it is not converged) stays with the
caller, exactly as ``git_reconcile`` leaves the merge to the agent. Replayable:
same (content, compact_fn) → same proof, always.

Pure stdlib. The compactor is any ``Callable[[str], str]`` — the real
``holistic_synthesiser.synthesise(...).text`` projection, a sub-string folder,
or a test fake; the kernel keys only on its observable input/output behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# A compactor maps content → its compacted form. The canonical real instance is
# the holistic fold (``synthesise(...).text``), but ANY pure string projection
# satisfies the contract — that is the whole point of injecting it (DIP): the
# kernel proves the fixed-point property of whatever fold it is handed.
Compactor = Callable[[str], str]


def is_compacted(content: str, compact_fn: Compactor) -> bool:
    """The FIXED-POINT criterion: ``compact_fn(content) == content``.

    True iff compacting the content is a NO-OP — i.e. ``content`` is already at
    the fold's fixed point ``x*``. This is the convergence criterion: the
    holistic kernel is converged for this content exactly when re-folding it
    changes nothing (mirrors ``git merge-tree`` reporting no conflict, and
    ``sanitise(content) == content`` reporting clean).

    It is a PROOF, not an assertion: the equality is computed against the
    compactor's actual output, so a fold that secretly mutates already-folded
    content fails this loudly rather than being trusted.
    """
    return compact_fn(content) == content


def is_idempotent(content: str, compact_fn: Compactor) -> bool:
    """VERIFY (don't assert) the fold's idempotency: ``ℱ∘ℱ == ℱ``.

    Checks ``compact_fn(compact_fn(content)) == compact_fn(content)`` — that
    applying the fold to an ALREADY-folded value is a no-op. This is the Banach
    idempotent-projection property ``holistic_synthesiser`` claims in prose
    (``ℱ(folded) = folded``); here it is demonstrated by double application
    rather than taken on faith.

    Note the difference from ``is_compacted``: ``is_compacted`` asks "is THIS
    input already at the fixed point?"; ``is_idempotent`` asks "does the fold
    LAND on a fixed point after one application, regardless of where the input
    started?". A correct fold is idempotent for ALL inputs even though most
    inputs are not themselves compacted. A fold that is non-idempotent is not a
    projection at all — the convergence claim is then false, and this returns
    False rather than masking it.
    """
    once = compact_fn(content)
    twice = compact_fn(once)
    return twice == once


def residual_bytes(content: str, compact_fn: Compactor) -> int:
    """The divergence metric ``d(content)`` — bytes that re-compaction removes.

    ``len(content) - len(compact_fn(content))`` in UTF-8 bytes: how much the
    fold would still strip from ``content``. Zero exactly at the fixed point
    (where compaction is a no-op), positive off it. This is the
    memory-compaction analogue of ``reconcile_fixed_point``'s
    ``residual_files`` and the size of ``scope_contamination``'s leaked spans —
    a concrete, monotone-toward-zero handle on "how far from converged".

    Defined as a byte delta (not a boolean) so the caller sees the MAGNITUDE of
    the residual, not merely its presence — the same reason ``git_reconcile``
    counts residual files. A fold that GROWS the content (negative delta) is
    pathological; we clamp at 0 because a non-fixed-point that expands is not a
    contraction and ``at_fixed_point`` already reports it as not converged.
    """
    delta = _utf8_len(content) - _utf8_len(compact_fn(content))
    return delta if delta > 0 else 0


def _utf8_len(text: str) -> int:
    """Byte length of ``text`` in UTF-8 — the unit the residual is measured in.

    Bytes, not code points, because the holistic kernel's own bound
    (``CAP_CHARS`` / the OCF 40k guard) is a size budget; the residual is most
    meaningful as the size the fold reclaims.
    """
    return len(text.encode("utf-8", "replace"))


@dataclass(frozen=True)
class CompactionProof:
    """A Banach fixed-point / idempotency witness for memory compaction.

    Mirrors ``git_reconcile.FixedPointProof`` and
    ``scope_contamination.ContaminationVerdict`` field-for-field in spirit:

    ``at_fixed_point`` — True iff compacting ``content`` is a NO-OP
                         (``is_compacted``): the content is at the fold's fixed
                         point ``x*``. This is the convergence criterion.
    ``residual_bytes`` — the divergence metric ``d``: bytes a re-compaction
                         would still strip. 0 at the fixed point, positive off
                         it. By construction ``at_fixed_point ⟺ residual_bytes
                         == 0`` (criterion and metric agree — a regression test
                         pins this exact equivalence, the same way
                         ``reconcile_fixed_point`` pins ``proven and
                         at_fixed_point ⟺ residual_files == 0``).
    ``idempotent``     — True iff ``ℱ∘ℱ == ℱ`` for this content
                         (``is_idempotent``): the fold lands on a fixed point
                         after one application. Verified, not assumed.
    ``metric_disagrees`` — True iff the no-op criterion and the byte metric
                         disagree (``at_fixed_point`` False yet residual 0): the
                         injected fold is a pathological length-preserving
                         rewrite, not a shrinking projection. An explicit flag so
                         callers detect the anomaly WITHOUT parsing ``rationale``.
                         False for every well-behaved fold.
    """

    at_fixed_point: bool
    residual_bytes: int
    idempotent: bool
    metric_disagrees: bool
    rationale: str


def _build_rationale(
    *, at_fp: bool, residual: int, idem: bool, metric_disagrees: bool
) -> str:
    """Plain-language rationale for a :class:`CompactionProof`.

    Extracted from :func:`compaction_proof` so the coordinator stays focused on
    computing the three signals (SRP/KISS); this owns only their wording.
    """
    if at_fp:
        idem_note = (
            "" if idem else " WARNING: fold is non-idempotent for this input "
            "(ℱ∘ℱ ≠ ℱ) — the convergence claim is unsound."
        )
        return (
            "re-compacting is a no-op — content is at the holistic fold's fixed "
            "point (residual 0 bytes); CONVERGED." + idem_note
        )
    anomaly = (
        "ANOMALY: the injected fold mutates content without shrinking its byte "
        "length (length-preserving rewrite) — reporting NOT-converged "
        "(fail-closed). " if metric_disagrees else ""
    )
    idem_note = "" if idem else " The fold is also non-idempotent here (ℱ∘ℱ ≠ ℱ)."
    return (
        anomaly
        + f"re-compacting would strip {residual} byte(s) — content is NOT at the "
        f"fold's fixed point; the holistic kernel is not yet converged for it."
        + idem_note
    )


def compaction_proof(content: str, compact_fn: Compactor) -> CompactionProof:
    """Prove (or refute) that ``content`` is at the memory-compaction fixed point.

    Coordinator: computes the no-op criterion (``is_compacted``), the divergence
    metric (``residual_bytes``), and the idempotency proof (``is_idempotent``)
    over the injected fold, then defers wording to :func:`_build_rationale`. For
    every well-behaved (shrinking-or-noop) fold the invariant
    ``at_fixed_point ⟺ residual_bytes == 0`` holds, and the no-op equality
    (``is_compacted``) is authoritative for ``at_fixed_point``.

    Fail-closed (NOT a bare ``assert``): a pathological fold that mutates content
    WITHOUT shrinking its byte length would break the equivalence (at_fp False
    yet residual 0). Rather than raise ``AssertionError`` into the caller — which
    a bare ``assert`` would, and ``python -O`` would strip — ``at_fixed_point``
    stays the no-op verdict (a non-no-op is NEVER reported converged) and the
    anomaly is surfaced in the rationale, matching ``auto_compact_policy``'s
    fail-closed discipline.

    Returns a ``CompactionProof`` carrying all three signals plus a
    plain-language rationale, so the caller never re-derives them.
    """
    at_fp = is_compacted(content, compact_fn)
    residual = residual_bytes(content, compact_fn)
    idem = is_idempotent(content, compact_fn)
    # at_fp ⟺ residual==0 for any shrinking-or-noop fold; only a length-preserving
    # mutation breaks it (at_fp False, residual 0). That disagreement is surfaced
    # by _build_rationale; at_fixed_point stays the authoritative no-op verdict.
    metric_disagrees = at_fp != (residual == 0)
    rationale = _build_rationale(
        at_fp=at_fp, residual=residual, idem=idem, metric_disagrees=metric_disagrees
    )
    return CompactionProof(
        at_fixed_point=at_fp,
        residual_bytes=residual,
        idempotent=idem,
        metric_disagrees=metric_disagrees,
        rationale=rationale,
    )


__all__ = [
    "Compactor",
    "is_compacted",
    "is_idempotent",
    "residual_bytes",
    "CompactionProof",
    "compaction_proof",
]
