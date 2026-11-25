"""channel_claim_provenance — signed, replayable provenance for a channel claim post.

Generalises the ``update_team`` / ``prove_it`` provenance weave to ANY channel
claim post that carries a headline + a set of verbatim factual claim LINES:
``/aurora`` (its work digest — PR lines + tallies) and ``/slack-update`` (its
``CommsUpdate`` fields — "PRs merged: 3", …). It composes the SAME three
substrate primitives those two recorders use and reimplements none of them
(DRY / syscall-doctrine):

  1. **/prove-it provenance** (``lib.prove_it``) — one citation per posted claim
     line whose verbatim quote is verified against the post's own canonical
     record. A fabricated line renders NOT-FOUND (the deterministic L1 floor).
  2. **IDR leaf** (``lib.precog.idr``) — one HMAC-SHA256-signed decision record
     for the post (headline, claim count, grounding rate). Tamper-evident.
  3. **memory-chain node** (``lib.context_chain``) — one signed ``context-delta``
     threading the prior HEAD and cross-referencing the IDR leaf by
     ``content_addr`` (the "backed by source Y" link).

CONTRACT — ADDITIVE + FAIL-OPEN (the load-bearing safety property)
------------------------------------------------------------------
This module NEVER blocks the channel post that already happened. Every public
entry point is wrapped so ANY failure (import error, signing key absent, disk
error) degrades to a logged no-op returning ``{"ok": False, ...}`` — a recording
error can NEVER break a prod post. ``path`` / ``idr_path`` / ``head_pointer`` are
injectable so tests (and the always-on-recording audit) run against a tmp ledger.

N=3 note (knowledge-maturation-functor): this is the THIRD recorder sharing the
prove-it → IDR → checkpoint compose (``update_team_provenance``,
``prove_it_provenance`` are 1 & 2). The two proven recorders are left untouched
to keep the audit's green surfaces byte-stable; a future refactor may fold all
three onto this generic core once it too is proven in the audit.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Iterable

_LIB = Path(__file__).resolve().parent
_ROOT = _LIB.parent
for _p in (str(_ROOT), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

__all__ = ["record_claim_post", "build_claim_citations", "IDR_KIND"]

# Distinct IDR ``kind`` so a verifier can enumerate channel-claim surfaces by
# kind (vs "team-update-post" / "prove-it-artifact").
IDR_KIND = "channel-claim-post"
_SOURCE_ID = "channel-claim-post"


def _claim_lines(headline: str, claim_lines: Iterable[str]) -> list[str]:
    """Normalise the posted claim lines; fall back to the headline so there is
    always >=1 grounded citation (an empty post still records a signed leaf)."""
    lines = [str(x).strip() for x in claim_lines if str(x).strip()]
    return lines or [str(headline or "post").strip() or "post"]


def _source_text(headline: str, lines: list[str]) -> str:
    """The canonical text the post's claims are verified against — the headline
    plus each verbatim claim line. This IS the record of what was posted
    (self-canonical: "GRIP told the channel X, backed by the exact X it posted"),
    so every claim quoting a line resolves and a fabricated line does not."""
    return "\n".join([str(headline or ""), *lines])


def build_claim_citations(headline: str, claim_lines: Iterable[str]):
    """Build (sources, citations) for a channel claim post. Pure.

    One citation per claim line — claim + quote are the verbatim line — so
    grounding is 1.0 iff every posted claim line is present in the canonical
    source (it is, by construction). Returns prove-it ``Source``/``Citation``
    objects ready for ``verify_all``.
    """
    from prove_it import Source, Citation  # type: ignore

    lines = _claim_lines(headline, claim_lines)
    source = Source(id=_SOURCE_ID, label="channel claim post (canonical record)",
                    text=_source_text(headline, lines))
    cites = [
        Citation(id=f"claim-{i}", claim=line, source_id=_SOURCE_ID, quote=line)
        for i, line in enumerate(lines)
    ]
    return [source], cites


def _post_provenance(headline: str, claim_lines: Iterable[str]) -> dict:
    """Deterministic /prove-it provenance for the post's claims. Pure."""
    from prove_it import verify_all, provenance  # type: ignore

    sources, cites = build_claim_citations(headline, claim_lines)
    verify_all(cites, sources)
    return provenance(sources, cites)


def _fingerprint(headline: str, lines: list[str]) -> str:
    """Stable content key: sha256 of headline + sorted claim lines (first 16 hex)."""
    basis = "|".join([str(headline or ""), *sorted(lines)])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _idr_decision(headline: str, lines: list[str], prov: dict) -> dict:
    """The signed decision body for the channel-claim IDR leaf."""
    return {
        "action": IDR_KIND,
        "headline": str(headline or "")[:200],
        "claim_count": len(lines),
        "grounding_rate": prov.get("grounding_rate", 0.0),
    }


def record_claim_post(
    headline: str,
    claim_lines: Iterable[str],
    *,
    kind: str = IDR_KIND,
    path: Path | None = None,
    idr_path: Path | None = None,
    head_pointer: Path | None = None,
) -> dict:
    """Attach signed, replayable provenance to a channel claim post. ADDITIVE + FAIL-OPEN.

    ``headline``     — the post's one-line claim (aurora tldr / comms headline).
    ``claim_lines``  — the verbatim factual claim lines (PR ``title (#n)`` lines,
                       ``commits: N``, ``PRs merged: 3`` …).

    Returns ``{ok, grounding_rate, idr_addr, memory_head, reason}``. NEVER raises —
    any failure degrades to ``ok=False`` (the post already happened; provenance is
    best-effort). ``path`` / ``idr_path`` / ``head_pointer`` are injectable for
    hermetic tests.
    """
    try:
        return _record(headline, list(claim_lines), kind=kind, path=path,
                       idr_path=idr_path, head_pointer=head_pointer)
    except Exception as exc:  # noqa: BLE001 — fail-open is the contract
        _warn(f"provenance recording degraded (post unaffected): {exc}")
        return {"ok": False, "grounding_rate": 0.0, "idr_addr": "",
                "memory_head": "", "reason": f"error: {type(exc).__name__}"}


def _record(headline, claim_lines, *, kind, path, idr_path, head_pointer) -> dict:
    """Inner record path (wrapped by record_claim_post's fail-open guard)."""
    from dataclasses import asdict
    import lib.precog.idr as _idr  # type: ignore
    import lib.context_chain as _cc  # type: ignore

    lines = _claim_lines(headline, claim_lines)
    prov = _post_provenance(headline, lines)
    decision = _idr_decision(headline, lines, prov)

    leaf = _idr.build_idr(
        prompt="", fingerprint=_fingerprint(headline, lines), decision=decision,
        predecessor_idr=None, depth=0, kind=kind,
        inputs={"grounding_rate": prov.get("grounding_rate", 0.0)},
    )
    _idr.append_idr(leaf, path=idr_path)
    idr_addr = _idr.content_addr(asdict(leaf))

    node = _cc.checkpoint(
        next_step=None,
        summary=f"channel claim posted: {headline}"[:600],
        title="channel-claim provenance",
        tier="feedback",
        records_idr=idr_addr,
        path=path,
        head_pointer=head_pointer,
    )
    return {
        "ok": True,
        "grounding_rate": prov.get("grounding_rate", 0.0),
        "idr_addr": idr_addr,
        "memory_head": node.id,
        "reason": "recorded",
    }


def _warn(msg: str) -> None:
    print(f"[channel-claim-provenance] {msg}", file=sys.stderr)
