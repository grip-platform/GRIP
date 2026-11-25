"""update_team_provenance — attach signed, replayable provenance to a team post.

When ``lib.update_team`` posts V>>'s work digest to the team, this module makes
the post itself a TAMPER-EVIDENT, REPLAYABLE record of the form *"GRIP told the
team X at time T, and X is backed by source Y"*. It composes three substrate
primitives the Cryptographic-Causation program already ships — it reimplements
none of them (DRY / syscall-doctrine):

  1. **/prove-it provenance** (``lib.prove_it``) — every factual claim in the
     digest (each merged-PR title+number, the commit count) is a citation whose
     verbatim quote is verified against the digest itself (the canonical record
     of what was posted). A fabricated claim renders NOT-FOUND — the deterministic
     L1 floor. Output: a provenance dict (per-source SHA-256 + grounding rate).
  2. **IDR leaf** (``lib.precog.idr``) — one HMAC-SHA256-signed decision record
     capturing the post decision (fingerprint, tldr, grounding rate, the exact
     PR numbers). Tamper-evident by construction.
  3. **memory-chain node** (``lib.context_chain``) — one signed ``context-delta``
     threading the prior HEAD and CROSS-REFERENCING the IDR leaf by its
     ``content_addr`` (so the chain replay can resolve *which* decision this post
     records). The memory of the post is itself auditable + replayable.

CONTRACT — additive + FAIL-OPEN (the load-bearing safety property)
-----------------------------------------------------------------
This module NEVER blocks ``update_team``'s existing post path. Every public
entry point is wrapped so that ANY failure of prove-it / IDR / memory-chain
(import error, signing key absent, disk error, malformed digest) degrades to a
logged no-op and returns a ``record`` dict with ``ok=False`` — the team post has
already happened by the time this runs, so its provenance is best-effort. When
the substrate is healthy the record carries the grounding rate, the IDR address,
and the new memory-chain HEAD id.

Purity / isolation: prove-it verification is pure; IDR + memory writes are the
only I/O, both routed through the existing append-with-flock shims. ``path`` /
``idr_path`` are injectable so tests run hermetically against a tmp ledger.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

_LIB = Path(__file__).resolve().parent
_ROOT = _LIB.parent
for _p in (str(_ROOT), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

__all__ = [
    "build_post_citations",
    "post_provenance",
    "record_post_provenance",
    "IDR_KIND",
]

# The IDR ``kind`` for a team-update decision leaf — distinct from a
# ``context-delta`` so a verifier can enumerate "team-update" surfaces by kind.
IDR_KIND = "team-update-post"

# Source id for the digest-as-canonical-record. The digest IS the source of
# truth for what was posted, so every claim is cited back to it.
_SOURCE_ID = "team-update-digest"


def _digest_source_text(digest: Any, summary: Any) -> str:
    """The canonical text the post's claims are verified against.

    Built deterministically from the digest's VERBATIM evidence tokens (PR
    title+number lines + the commit/test/hypothesis tallies) plus the tldr — so
    a claim quoting any of those resolves, and a fabricated claim does not. This
    is the "source Y" in "GRIP told the team X, backed by Y": Y is the exact
    record of the work that was posted.
    """
    lines = [str(getattr(summary, "tldr", "") or "")]
    for pr in getattr(digest, "merged_prs", ()) or ():
        lines.append(f"{pr.title} (#{pr.number})")
    lines.append(
        f"commits: {getattr(digest, 'commit_count', 0)} "
        f"tests: {getattr(digest, 'test_units', 0)} "
        f"hypotheses: {getattr(digest, 'hypothesis_count', 0)}"
    )
    return "\n".join(lines)


def build_post_citations(digest: Any, summary: Any):
    """Build (sources, citations) for the post's factual claims. Pure.

    One citation per merged PR (claim = the PR landed; quote = its verbatim
    ``title (#number)`` line) plus one for the commit tally. Returns prove-it
    ``Source``/``Citation`` objects ready for ``verify_all``. Importing prove_it
    here is safe — it is stdlib-only and pure.
    """
    from prove_it import Source, Citation  # type: ignore

    text = _digest_source_text(digest, summary)
    source = Source(id=_SOURCE_ID, label="team-update digest (canonical post record)", text=text)
    cites = []
    for pr in getattr(digest, "merged_prs", ()) or ():
        line = f"{pr.title} (#{pr.number})"
        cites.append(Citation(
            id=f"pr-{pr.number}",
            claim=f"PR #{pr.number} ({pr.type}) shipped: {pr.title}",
            source_id=_SOURCE_ID,
            quote=line,
        ))
    commit_quote = f"commits: {getattr(digest, 'commit_count', 0)}"
    cites.append(Citation(
        id="commits",
        claim=f"{getattr(digest, 'commit_count', 0)} commits landed on origin/main",
        source_id=_SOURCE_ID,
        quote=commit_quote,
    ))
    return [source], cites


def post_provenance(digest: Any, summary: Any) -> dict:
    """Deterministic /prove-it provenance for the post's claims. Pure.

    Same (digest, summary) → same provenance dict (modulo the ``generated``
    timestamp), so it is replayable. Carries per-source SHA-256, per-citation
    status, and the grounding rate (1.0 when every claim is verbatim-backed).
    """
    from prove_it import verify_all, provenance  # type: ignore

    sources, cites = build_post_citations(digest, summary)
    verify_all(cites, sources)
    return provenance(sources, cites)


def _post_fingerprint(digest: Any) -> str:
    """Stable content key for the post (sorted PR numbers + commit count).

    Mirrors ``update_team._fingerprint`` semantics deliberately so the IDR leaf's
    fingerprint matches the dedup fingerprint — one signal, two consumers.
    """
    pr_numbers = sorted(pr.number for pr in (getattr(digest, "merged_prs", ()) or ()))
    basis = f"{pr_numbers}|{getattr(digest, 'commit_count', 0)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _idr_decision(digest: Any, summary: Any, prov: dict) -> dict:
    """The signed decision body for the team-update IDR leaf.

    Records WHAT the team was told (tldr + exact PR numbers) and the provenance
    grounding rate — so the leaf, once signed, is a tamper-evident statement of
    "GRIP posted this work, this well-grounded, at this fingerprint".
    """
    return {
        "action": "team-update-post",
        "tldr": str(getattr(summary, "tldr", "") or ""),
        "pr_numbers": sorted(pr.number for pr in (getattr(digest, "merged_prs", ()) or ())),
        "commit_count": int(getattr(digest, "commit_count", 0)),
        "grounding_rate": prov.get("grounding_rate", 0.0),
    }


def record_post_provenance(
    digest: Any,
    summary: Any,
    *,
    path: Path | None = None,
    idr_path: Path | None = None,
    head_pointer: Path | None = None,
) -> dict:
    """Attach signed, replayable provenance to a team post. ADDITIVE + FAIL-OPEN.

    Composes the three primitives: prove-it provenance → signed IDR leaf →
    memory-chain node cross-referencing the leaf. Returns a small record dict:
    ``{ok, grounding_rate, idr_addr, memory_head, reason}``. NEVER raises — any
    failure degrades to ``ok=False`` with a reason and a stderr log, because the
    team post itself has already happened and its provenance is best-effort.

    ``path``/``idr_path``/``head_pointer`` are injectable for hermetic tests.
    """
    try:
        return _record(digest, summary, path=path, idr_path=idr_path, head_pointer=head_pointer)
    except Exception as exc:  # noqa: BLE001 — fail-open is the contract
        _warn(f"provenance recording degraded (post unaffected): {exc}")
        return {"ok": False, "grounding_rate": 0.0, "idr_addr": "",
                "memory_head": "", "reason": f"error: {type(exc).__name__}"}


def _record(digest, summary, *, path, idr_path, head_pointer) -> dict:
    """Inner record path (wrapped by record_post_provenance's fail-open guard)."""
    from dataclasses import asdict
    # Import via the repo-root package path (lib.*) so module IDENTITY matches
    # callers and tests. A bare `from precog.idr import ...` binds a DIFFERENT
    # module object (precog.idr vs lib.precog.idr both resolve the same file under
    # the dual sys.path), so the fail-open monkeypatch in the tests missed and the
    # weave reported ok=True even when the IDR primitive raised.
    import lib.precog.idr as _idr  # type: ignore
    import lib.context_chain as _cc  # type: ignore

    prov = post_provenance(digest, summary)
    fingerprint = _post_fingerprint(digest)
    decision = _idr_decision(digest, summary, prov)

    leaf = _idr.build_idr(
        prompt="", fingerprint=fingerprint, decision=decision,
        predecessor_idr=None, depth=0, kind=IDR_KIND,
        inputs={"grounding_rate": prov.get("grounding_rate", 0.0)},
    )
    _idr.append_idr(leaf, path=idr_path)
    idr_addr = _idr.content_addr(asdict(leaf))

    node = _cc.checkpoint(
        next_step=None,
        summary=f"team-update posted: {decision['tldr']}"[:600],
        title="team-update provenance",
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
    print(f"[update-team-provenance] {msg}", file=sys.stderr)
