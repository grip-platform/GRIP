"""Generic idempotent-fold kernel — the idempotent-fold shim (extracted at N=4; the registry below now tracks eight domains).

GRIP's independent idempotent-fold domains all share the same shape:

    git_reconcile.py        (merge-tree no-op ↔ branch reconciled)
    scope_contamination.py  (sanitise no-op ↔ content clean)
    compaction_idempotence.py (compact no-op ↔ memory converged)
    insights_dedup.py       (dedup no-op ↔ store converged)
    evidence_replay.py      (replay-normalise no-op ↔ ledger effective; domain #5, first ROUTED THROUGH this kernel rather than extracted before it)
    lexicon/codec.py        (encode no-op ↔ kernel written in defined terms; domain #6, KERN)

All four independently discovered the same theorem: **a fold F is
converged iff F(x) == x (fixed point) AND idempotent iff F(F(x)) == F(x)**.
The divergence metric (how far from convergence) is domain-specific but
always monotone-toward-zero at the fixed point.

This module extracts the THREE invariants the registered domains all mechanically
enforce, into a single typed Protocol + generic helpers — so a NEW domain
implements only the minimal hooks and gets idempotence-verification and a
Proof dataclass for free.

Origin: knowledge-maturation-functor.md N=4 (three independent instances earn
a rule; four earn the shim with the soundness invariant IN the shim itself).
The registered instances are tracked by the ``KNOWN_DOMAINS`` registry below.

Design contracts (enforced IN this shim, not per-callsite):
  1. ``is_at_fixed_point(domain, x) ↔ residual_metric(domain, x) == zero_residual``
     This equivalence is the Banach criterion: at the fixed point the metric is
     zero, off it the metric is positive.  The shim surfaces disagreement in
     ``GenericProof.metric_disagrees`` (fail-closed — never reports converged
     when the fold still mutates the input).
  2. ``is_idempotent(domain, x) ↔ domain.fold(domain.fold(x)) == domain.fold(x)``
     Verified by double-application — never asserted.  A fold that is
     non-idempotent is not a projection and the convergence claim is unsound.
  3. The residual metric is NEVER negative (clamped at zero_residual).  A fold
     that grows its input is pathological; the shim still reports not-converged
     (fail-closed) and surfaces the anomaly.

Pure stdlib.  No I/O.  The fold function and residual computation are injected
(DIP) so tests run with zero side-effects.

Relationship to the four existing modules: they remain the AUTHORITATIVE
implementations for their domains (their public APIs and tests are unchanged).
This shim GENERALISES the common skeleton they all independently implemented.
New domains use this shim directly; existing domains may optionally delegate
their ``is_idempotent`` / proof-assembly to it, but are not required to.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

# ── Type variable for domain input ──────────────────────────────────────────

T = TypeVar("T")  # the type of the value a fold operates on (str, list, …)
R = TypeVar("R")  # the type of the residual metric (int, float, …)


# ── Residual zero sentinel protocol ─────────────────────────────────────────

class ResidualZero(Protocol):
    """The additive identity for a residual metric — the value at fixed point.

    Domains return an ``int`` (residual_bytes, residual_duplicates, residual_files),
    a ``float``, or any comparable type where ``zero`` is the fixed-point indicator.
    The Protocol lets the shim work with any comparable without importing each
    domain's sentinel value.
    """

    def __eq__(self, other: object) -> bool: ...  # noqa: D105


# ── The Domain Protocol ──────────────────────────────────────────────────────

class IdempotentDomain(Protocol[T, R]):
    """Minimal contract a domain must satisfy to use the generic shim.

    A domain is anything with:
      * ``fold(x: T) -> T``     — the idempotent transformation
      * ``residual(x: T) -> R`` — the divergence metric (zero AT the fixed point)
      * ``zero_residual: R``    — the identity value for the residual (e.g. 0, 0.0)

    The fold function must be a pure function: same input → same output, no
    side-effects.  The residual must be >= zero_residual and == zero_residual
    exactly when fold(x) == x (the no-op criterion and the metric agree).
    """

    def fold(self, x: T) -> T:
        """Apply the idempotent transformation once.

        Must be pure and deterministic.  The shim calls it at most twice
        (for the idempotency double-application check).
        """
        ...

    def residual(self, x: T) -> R:
        """Divergence metric: how far ``x`` is from the fold's fixed point.

        Must equal ``self.zero_residual`` exactly when ``self.fold(x) == x``.
        Must be >= ``self.zero_residual`` (the shim clamps at zero_residual
        only when it detects a pathological negative residual; well-behaved
        domains never need the clamp).
        """
        ...

    @property
    def zero_residual(self) -> R:
        """The metric value at the fixed point (usually 0 or 0.0)."""
        ...


# ── The three soundness invariants (enforced HERE, not per-callsite) ─────────

def is_at_fixed_point(domain: IdempotentDomain[T, Any], x: T) -> bool:
    """Invariant 1 — fixed-point criterion: ``domain.fold(x) == x``.

    A value is at the fixed point iff applying the fold is a no-op.  This is
    the Banach convergence criterion: the fold has nothing more to do.

    This is the PRIMARY criterion; the residual metric should agree with it
    (``residual == zero_residual``).  Agreement is checked in
    :func:`generic_proof` and surfaced in ``GenericProof.metric_disagrees``.
    """
    return domain.fold(x) == x


def is_idempotent(domain: IdempotentDomain[T, Any], x: T) -> bool:
    """Invariant 2 — idempotency: ``fold(fold(x)) == fold(x)`` (ℱ∘ℱ == ℱ).

    The shim enforces this by DOUBLE APPLICATION, never by assertion.  A fold
    that is non-idempotent is not a projection and the convergence claim is
    unsound; this returns False rather than masking the defect.

    Note the distinction from :func:`is_at_fixed_point`: ``is_at_fixed_point``
    asks "is THIS input already at the fixed point?"; ``is_idempotent`` asks
    "does the fold LAND on a fixed point after one application, regardless of
    where the input started?".  A correct fold is idempotent for ALL inputs
    even though most inputs are not themselves at the fixed point.
    """
    once = domain.fold(x)
    twice = domain.fold(once)
    return twice == once


def idempotence_theorem(domain: IdempotentDomain[T, Any], x: T) -> bool:
    """The SOUNDNESS INVARIANT enforced in this shim: ``fold(fold(x)) == fold(x)``.

    This is the canonical name callers use to assert the theorem holds for a
    concrete (domain, value) pair.  It is identical to :func:`is_idempotent`
    but named for its role as the invariant the shim GUARANTEES — the reason
    the shim exists at all (per knowledge-maturation-functor.md: "the soundness
    invariant enforced IN the shim itself, not per-callsite").

    Raises ``IdempotenceViolation`` when the theorem fails — the fold is NOT a
    projection and any convergence claim built on it is unsound.  Callers that
    do not want an exception use :func:`is_idempotent` directly.
    """
    if not is_idempotent(domain, x):
        raise IdempotenceViolation(
            f"Idempotence theorem FAILED for domain {domain!r}: "
            "fold(fold(x)) != fold(x). The fold is not a projection; "
            "any convergence claim built on it is UNSOUND."
        )
    return True


class IdempotenceViolation(AssertionError):
    """Raised by :func:`idempotence_theorem` when F∘F ≠ F.

    Inherits ``AssertionError`` so callers expecting a standard assertion
    failure catch it; it carries a plain-language message per Rule 17.
    """


# ── The generic Proof dataclass ──────────────────────────────────────────────

@dataclass(frozen=True)
class GenericProof(Generic[T, R]):
    """Banach fixed-point / idempotency witness for any idempotent domain.

    Field semantics mirror the four existing domain-specific Proof dataclasses
    (``FixedPointProof``, ``ContaminationVerdict``, ``CompactionProof``,
    ``DedupProof``) — a new domain using this shim gets all three signals plus
    a plain-language rationale for free.

    ``at_fixed_point``  — True iff ``domain.fold(x) == x``: the value is at
                          the fold's fixed point.  The PRIMARY convergence
                          criterion (Invariant 1).
    ``residual``        — the divergence metric value at the time of the proof.
                          Equals ``domain.zero_residual`` exactly at the fixed
                          point (for a well-behaved fold).
    ``idempotent``      — True iff ``fold(fold(x)) == fold(x)`` (Invariant 2).
                          Verified by double application.
    ``metric_disagrees``— True iff ``at_fixed_point`` and
                          ``residual == domain.zero_residual`` disagree.  Should
                          never be True for a well-behaved fold; surfaced as an
                          explicit flag so callers detect the anomaly without
                          parsing ``rationale`` (fail-closed: a disagreement
                          never reports converged).
    ``rationale``       — plain-language explanation (Rule 17 compliant).
                          Excluded from equality comparison (prose, not data).
    """
    at_fixed_point: bool
    residual: R
    idempotent: bool
    metric_disagrees: bool
    rationale: str = field(compare=False)


# ── The generic proof coordinator ────────────────────────────────────────────

def generic_proof(domain: IdempotentDomain[T, R], x: T) -> GenericProof[T, R]:
    """Prove (or refute) that ``x`` is at the fixed point of ``domain.fold``.

    Coordinator: computes all three invariant signals over the injected domain,
    builds the rationale, and returns a :class:`GenericProof`.  The soundness
    invariants (both the no-op check and the double-application idempotency) are
    computed HERE, not delegated to the caller — that is the point of the shim.

    Fail-closed: a pathological fold that mutates ``x`` without changing the
    residual (length-preserving mutation) surfaces as ``metric_disagrees=True``
    and ``at_fixed_point=False`` (the no-op equality is authoritative).  This
    matches the fail-closed discipline in ``compaction_idempotence.compaction_proof``
    and ``insights_dedup.dedup_proof``.
    """
    at_fp = is_at_fixed_point(domain, x)
    res = domain.residual(x)
    idem = is_idempotent(domain, x)
    metric_disagrees = at_fp != (res == domain.zero_residual)
    rationale = _build_generic_rationale(
        domain=domain, at_fp=at_fp, residual=res, idem=idem,
        metric_disagrees=metric_disagrees,
    )
    return GenericProof(
        at_fixed_point=at_fp,
        residual=res,
        idempotent=idem,
        metric_disagrees=metric_disagrees,
        rationale=rationale,
    )


def _build_generic_rationale(
    *,
    domain: IdempotentDomain[Any, Any],
    at_fp: bool,
    residual: Any,
    idem: bool,
    metric_disagrees: bool,
) -> str:
    """Plain-language rationale for a :class:`GenericProof` (Rule 17).

    SRP: this function's only job is wording — the coordinator computes the
    signals, this translates them into human-readable text.
    """
    domain_name = type(domain).__name__
    if at_fp:
        idem_note = (
            ""
            if idem
            else (
                f" WARNING: the fold of {domain_name} is non-idempotent for "
                "this input (ℱ∘ℱ ≠ ℱ) — the convergence claim is UNSOUND."
            )
        )
        return (
            f"re-folding via {domain_name} is a no-op — value is at the "
            f"fixed point (residual={residual!r}); CONVERGED." + idem_note
        )
    anomaly = (
        f"ANOMALY: residual is {residual!r} (== zero) yet at_fixed_point is "
        "False — the fold is a length-preserving mutation, not a shrinking "
        "projection. Reporting NOT-converged (fail-closed). "
        if metric_disagrees
        else ""
    )
    idem_note = (
        ""
        if idem
        else f" The fold of {domain_name} is also non-idempotent (ℱ∘ℱ ≠ ℱ)."
    )
    return (
        anomaly
        + f"re-folding via {domain_name} would change the value "
        f"(residual={residual!r}) — NOT at the fixed point; not converged."
        + idem_note
    )


# ── Known-domains registry (for conformance tests) ───────────────────────────

# Each entry names a module + the public is_idempotent function it provides, so
# tests/test_dogfood_kernel.py can verify each domain satisfies the Protocol
# contract without importing the shim directly in those domain modules.
KNOWN_DOMAINS: list[dict[str, str]] = [
    {
        "module": "lib.git_reconcile",
        "description": "git merge-tree fixed-point",
        "is_idempotent_fn": "is_idempotent",
    },
    {
        "module": "lib.scope_contamination",
        "description": "scope-contamination sanitise no-op",
        "note": "is_idempotent checked via sanitise(sanitise(x)) == sanitise(x)",
    },
    {
        "module": "lib.compaction_idempotence",
        "description": "holistic memory-compaction fixed-point",
        "is_idempotent_fn": "is_idempotent",
    },
    {
        "module": "lib.insights_dedup",
        "description": "insights-store deduplication fixed-point",
        "is_idempotent_fn": "is_idempotent",
    },
    {
        "module": "lib.evidence_replay",
        "description": "evidence-ledger replay normalisation fixed-point",
        "note": "conformance via EvidenceLedgerDomain -> generic_proof (its "
                "module-level is_idempotent takes (events, schema), so the "
                "single-argument is_idempotent_fn convention does not apply)",
    },
    {
        "module": "lib.lexicon.codec",
        "description": "KERN vocabulary codec fixed-point (encode to defined terms)",
        "note": "conformance via KernCodecDomain(Codec(pairs)) -> generic_proof; "
                "the domain is built from a vocabulary, so the single-argument "
                "is_idempotent_fn convention does not apply",
    },
    {
        "module": "lib.rea_evidence",
        "description": "REA reverse-engineering evidence fixed-point",
        "note": "conformance via ReaEvidenceDomain -> generic_proof",
    },
    {
        "module": "lib.stack_dogfood.model",
        "description": "stack-dogfood install-convergence fixed-point "
                       "(every product registered in every harness)",
        "note": "conformance via InstallDomain() over a Census -> generic_proof; "
                "the domain folds a product-by-harness census, so the "
                "single-argument is_idempotent_fn convention does not apply",
    },
]


__all__ = [
    "T",
    "R",
    "ResidualZero",
    "IdempotentDomain",
    "is_at_fixed_point",
    "is_idempotent",
    "idempotence_theorem",
    "IdempotenceViolation",
    "GenericProof",
    "generic_proof",
    "KNOWN_DOMAINS",
]
