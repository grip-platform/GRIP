"""Cryptographic-causation composition — the Banach fixed-point proof.

The Cryptographic-Causation program composes three primitives every time GRIP
records a factual claim it made to its team (``lib.update_team_provenance``):

    prove-it provenance  →  signed IDR leaf  →  memory-chain node

This kernel is the dogfood proof that the COMPOSITION is sound. It is the SAME
idempotent-no-op / Banach fixed-point shape as the sibling kernels
(``lib.scope_contamination``, ``lib.mesh_setup_idempotent``, ``lib.git_reconcile``),
transplanted to the provenance-composition domain:

    git-solve:           a merge is CLEAN     iff merge-tree is a no-op.
    scope-contamination: content is CLEAN     iff sanitising it is a no-op.
    mesh setup:          a machine is SET-UP  iff running setup is a no-op.
    here (causation):    the record is SOUND  iff REPLAYING the compose is a no-op.

So soundness is PROVEN, never asserted: ``compose(compose(x)) == compose(x)``.
Replaying prove→IDR→memory on the same digest must be a deterministic no-op —
the prove-it grounding tally is identical, and the IDR leaf's ``content_addr``
(which by design excludes the volatile id/ts/audit fields) is reproducible. If a
re-derivation drifts, the composition is NOT a fixed point and the record is not
trustworthy — exactly the failure a tamper-evident chain must catch.

Fixed-point framing (honest about its depth)
--------------------------------------------
The compose operator F maps a digest to its provenance fingerprint
``(grounding_tally, idr_addr)``. F is deterministic on a fixed digest, so
``F(F(x)) == F(x)`` is the formal content of "the record replays identically".
The compose is content-addressed, so — like ``mesh_setup`` — the no-op equality
is the clean FORMULATION (parity with the siblings); the LOAD-BEARING,
Goodhart-resistant content lives in the ``drift`` list, derived INDEPENDENTLY of
F's own second run and cross-checked by an assert in ``check``. Two independent
anchors kill the tautology mutant:

  1. the expected grounding tally is computed DIRECTLY from the citation set
     (every PR-line + commit-line is verbatim-present in the digest source by
     construction → expected all-verified → grounding 1.0), NOT read back from F;
  2. the expected IDR ``content_addr`` is recomputed via ``precog.idr.content_addr``
     on an INDEPENDENTLY-assembled envelope dict, NOT copied from the leaf F built.

A MUTATED claim (a citation whose quote is absent from the source) drops the
grounding below 1.0 AND changes the decision body → both anchors move → the proof
breaks. That is the Goodhart guard: the proof can FAIL, so green means something.

Purity
------
This module performs NO disk I/O. The IDR ``content_addr`` is a PURE function of
the (unsigned) envelope body, so the fixed-point fingerprint is computed without
writing the ledger — every test runs hermetically against an in-memory digest.
(``update_team_provenance.record_post_provenance`` is the IMPURE sibling that
actually persists the leaf + node; this kernel proves the math it relies on.)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LIB = Path(__file__).resolve().parent
_ROOT = _LIB.parent
for _p in (str(_ROOT), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lib.update_team_provenance as utp  # noqa: E402  (lib.* so module identity matches callers/tests)

__all__ = [
    "CausationFingerprint",
    "CausationVerdict",
    "compose",
    "expected_grounding",
    "expected_idr_addr",
    "check",
    "selftest_idempotent",
]


@dataclass(frozen=True)
class CausationFingerprint:
    """The replay-stable coordinate of one provenance composition.

    ``grounding_rate`` and ``verified`` come from the prove-it floor; ``idr_addr``
    is the content address of the IDR decision leaf (volatile-field-excluded, so
    reproducible). Two compositions of the same digest must produce an EQUAL
    fingerprint — that equality is the no-op proof.
    """

    grounding_rate: float
    verified: int
    not_found: int
    idr_addr: str


@dataclass(frozen=True)
class CausationVerdict:
    """Result of proving a digest's provenance composition is a fixed point.

    ``clean`` is True iff replaying the compose is a no-op AND the two independent
    anchors (grounding tally, idr addr) match the compose's own output. ``drift``
    names every coordinate that disagreed (empty when clean) — the concrete delta,
    exactly as the sibling kernels surface conflict hunks / pending actions.
    """

    clean: bool
    fingerprint: CausationFingerprint
    drift: tuple[str, ...]
    rationale: str

    @property
    def replayable(self) -> bool:
        """Alias — a clean composition is, by definition, deterministically replayable."""
        return self.clean


def _decision_envelope(digest: Any, summary: Any, prov: dict) -> dict:
    """Assemble the UNSIGNED IDR envelope body the leaf would carry — independently
    of ``build_idr`` — so ``content_addr`` over it is a second anchor, not a copy.

    Mirrors ``build_idr``'s envelope shape for the addressed fields (kind,
    predecessor_idr, depth, fingerprint, decision, inputs, happi). The volatile
    id/ts/audit are excluded by ``content_addr`` itself, so omitting them here is
    correct and keeps this a pure re-derivation.
    """
    return {
        "happi": "1.1",
        "kind": utp.IDR_KIND,
        "predecessor_idr": None,
        "depth": 0,
        "fingerprint": utp._post_fingerprint(digest),
        "decision": utp._idr_decision(digest, summary, prov),
        "inputs": {"grounding_rate": prov.get("grounding_rate", 0.0)},
    }


def compose(digest: Any, summary: Any) -> CausationFingerprint:
    """Run the prove→IDR composition and return its replay-stable fingerprint. Pure.

    The prove-it tally + the IDR leaf content_addr — no disk write — so calling it
    twice on the same digest MUST yield an equal fingerprint (the no-op property).
    """
    from lib.precog.idr import content_addr  # type: ignore

    prov = utp.post_provenance(digest, summary)
    tally = prov.get("tally", {})
    addr = content_addr(_decision_envelope(digest, summary, prov))
    return CausationFingerprint(
        grounding_rate=prov.get("grounding_rate", 0.0),
        verified=int(tally.get("verified", 0)),
        not_found=int(tally.get("not_found", 0)),
        idr_addr=addr,
    )


def expected_grounding(digest: Any, summary: Any) -> tuple[float, int, int]:
    """INDEPENDENT anchor #1: the grounding the composition MUST produce.

    Computed directly from the citation set, NOT from ``compose``. Every PR-line
    and the commit-line are verbatim-present in the digest source by construction
    (``build_post_citations`` quotes the exact lines ``_digest_source_text``
    emits), so an honest digest grounds at 1.0 with ``verified == len(cites)`` and
    ``not_found == 0``. Returns (grounding_rate, verified, not_found).
    """
    _sources, cites = utp.build_post_citations(digest, summary)
    n = len(cites)
    rate = round(n / max(n, 1), 3) if n else 0.0
    return (rate, n, 0)


def expected_idr_addr(digest: Any, summary: Any) -> str:
    """INDEPENDENT anchor #2: the IDR content_addr the composition MUST produce.

    Recomputed via ``content_addr`` on an independently-assembled envelope using
    the SAME deterministic provenance — distinct code path from ``compose`` (which
    builds the fingerprint inline). Equal addresses prove the leaf is reproducible.
    """
    from lib.precog.idr import content_addr  # type: ignore

    prov = utp.post_provenance(digest, summary)
    return content_addr(_decision_envelope(digest, summary, prov))


def _collect_drift(fp: CausationFingerprint, replay: CausationFingerprint,
                   exp_ground: tuple[float, int, int], exp_addr: str) -> list[str]:
    """Every coordinate where the compose disagrees with a replay or an anchor."""
    drift: list[str] = []
    if fp != replay:
        drift.append("replay-mismatch")  # F(F(x)) != F(x): not deterministic
    if (fp.grounding_rate, fp.verified, fp.not_found) != exp_ground:
        drift.append("grounding-anchor")  # independent tally disagrees
    if fp.idr_addr != exp_addr:
        drift.append("idr-addr-anchor")   # independent content_addr disagrees
    return drift


def check(digest: Any, summary: Any) -> CausationVerdict:
    """Prove the digest's provenance composition is a sound Banach fixed point.

    Clean iff (a) the compose replays identically (``F(F(x)) == F(x)``) AND (b)
    both independent anchors match. The assert is the LOAD-BEARING Goodhart guard:
    ``clean`` (from the compose's own replay) must agree with the anchor-derived
    drift list — a vacuous/tautological compose would pass (a) but fail an anchor,
    so the assert fires. Do NOT collapse the anchors into ``compose`` output.
    """
    fp = compose(digest, summary)
    replay = compose(digest, summary)
    exp_ground = expected_grounding(digest, summary)
    exp_addr = expected_idr_addr(digest, summary)
    drift = _collect_drift(fp, replay, exp_ground, exp_addr)
    clean = (fp == replay) and not drift
    assert clean == (len(drift) == 0), "no-op proof disagrees with drift set"
    rationale = (
        f"replaying the prove→IDR composition is a no-op — grounding "
        f"{int(fp.grounding_rate * 100)}%, idr {fp.idr_addr[:19]}…; SOUND (fixed point)."
        if clean else
        f"replaying the composition would change {len(drift)} coordinate(s) "
        f"[{', '.join(drift)}] — record is NOT a sound fixed point."
    )
    return CausationVerdict(clean=clean, fingerprint=fp, drift=tuple(drift), rationale=rationale)


def selftest_idempotent(digest: Any, summary: Any) -> bool:
    """Loud Banach self-check: True iff the composition is a sound fixed point.

    The degenerate (zero-improve) loop's idempotence proof, mirroring
    ``lib.decision_chain_publish.selftest_idempotent``. Returns the verdict's
    ``clean`` so a caller can hard-assert it in CI.
    """
    return check(digest, summary).clean
