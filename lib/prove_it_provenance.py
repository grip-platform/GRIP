"""prove_it_provenance — attach signed, replayable provenance to a /prove-it artifact.

Mirrors ``lib.update_team_provenance.record_post_provenance`` exactly: composes
three substrate primitives without reimplementing any of them (DRY / syscall-doctrine):

  1. **/prove-it provenance** — the spec's grounding_rate + tally (from render()).
  2. **IDR leaf** (``lib.precog.idr``) — HMAC-SHA256-signed decision record capturing
     the prove-it decision (title, grounding_rate, citation counts). Tamper-evident.
  3. **memory-chain node** (``lib.context_chain``) — signed context-delta cross-
     referencing the IDR leaf by content_addr (the 'backed by Y' link).

CONTRACT — ADDITIVE + FAIL-OPEN (load-bearing safety property)
--------------------------------------------------------------
This module NEVER blocks /prove-it's existing render path. record_proveit_provenance
NEVER raises — any failure degrades to ``{"ok": False, ...}`` + a stderr line.
``path`` / ``idr_path`` / ``head_pointer`` are injectable for hermetic tests.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_LIB = Path(__file__).resolve().parent
_ROOT = _LIB.parent
for _p in (str(_ROOT), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

__all__ = ["record_proveit_provenance", "support_attestations_sha256", "IDR_KIND"]

# The IDR ``kind`` for a /prove-it artifact decision leaf — distinct from
# "team-update-post" so verifiers can enumerate "prove-it" surfaces by kind.
IDR_KIND = "prove-it-artifact"


def _fingerprint(spec: dict) -> str:
    """Stable content key: sha256 of title + sorted citation ids (first 16 hex chars)."""
    title = spec.get("title", "")
    cids = sorted(str(c.get("id", "")) for c in spec.get("citations", []))
    basis = f"{title}|{cids}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bound_entry(c: dict, spec_c: dict, sources: dict) -> list:
    """One attested citation as ``[id, attestation, binding]``: the binding names the
    claim, the quote and the source snapshot the verdict was given about, so the same
    attestation cannot be moved onto different text under the same id (Codex, GRIP#8000)."""
    source = sources.get(c.get("source_id"), {}) if isinstance(sources, dict) else {}
    binding = {"claim_sha256": _sha256_text(str(spec_c.get("claim", ""))),
               "quote_sha256": _sha256_text(str(spec_c.get("quote", ""))),
               "source_id": str(c.get("source_id", "")),
               "source_sha256": str(source.get("sha256", "")) if isinstance(source, dict) else "",
               "start": c.get("start"), "end": c.get("end"), "status": c.get("status")}
    return [str(c.get("id", "")), c["support_attestation"], binding]


def _paired(spec: dict, prov: dict) -> list:
    """(provenance citation, spec citation) pairs, matched by POSITION, never by id.

    render() builds the provenance citations from spec["citations"] in order, so the
    i-th of each is the same citation. Looking a spec citation up by id instead bound
    every row sharing a duplicate id to the first one, so a second "c1"'s claim and
    quote could change without changing the digest (Codex, GRIP#8000). A spec and a
    provenance that do not line up raise, so nothing is sealed against the wrong text;
    record_proveit_provenance turns that into its fail-open ok=False."""
    pcs = [c for c in prov.get("citations", []) or []]
    scs = [c for c in spec.get("citations", []) or []]
    if len(pcs) != len(scs) or any(
            not isinstance(pc, dict) or not isinstance(sc, dict)
            or str(pc.get("id", "")) != str(sc.get("id", "")) for pc, sc in zip(pcs, scs)):
        raise ValueError("spec and provenance citations do not line up; attestations not sealed")
    return list(zip(pcs, scs))


def support_attestations_sha256(spec: dict, prov: dict) -> str:
    """SHA-256 of the support attestations in ``prov``, or "" when no citation has one.

    The digest covers, in citation order, ``[citation id, attestation, binding]`` for
    every citation carrying a ``support_attestation``, as canonical JSON (sorted keys,
    compact separators). The binding holds SHA-256 digests of the citation's claim and
    quote (from the spec citation at the same position), the source's text digest (from
    ``prov["sources"]``), and the located offsets and status. Citation order is kept
    deliberately: the same attestations on different citations are a different record.
    Without this the signed decision named only the title, grounding rate and tallies, so
    a citation's attested verdict, model, probabilities or question digest -- or the text
    it was given about -- could change and the signed record would not (Codex, GRIP#7991
    and GRIP#8000). Raises ValueError when attestations exist but the spec and the
    provenance citations do not line up position by position."""
    if not any(isinstance(c, dict) and c.get("support_attestation")
               for c in prov.get("citations", []) or []):
        return ""
    sources = prov.get("sources", {})
    entries = [_bound_entry(pc, sc, sources) for pc, sc in _paired(spec, prov)
               if pc.get("support_attestation")]
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _idr_decision(spec: dict, prov: dict) -> dict:
    """The signed decision body for the prove-it IDR leaf.

    ``support_attestations_sha256`` is added only when at least one citation carries a
    support attestation, so a record without any is byte-identical to before."""
    tally = prov.get("tally", {})
    decision = {
        "action": "prove-it-artifact",
        "title": spec.get("title", ""),
        "grounding_rate": prov.get("grounding_rate", 0.0),
        "tally_verified": tally.get("verified", 0),
        "tally_fuzzy": tally.get("fuzzy", 0),
        "tally_not_found": tally.get("not_found", 0),
    }
    digest = support_attestations_sha256(spec, prov)
    if digest:
        decision["support_attestations_sha256"] = digest
    return decision


def record_proveit_provenance(
    spec: dict,
    prov: dict,
    *,
    path: Path | None = None,
    idr_path: Path | None = None,
    head_pointer: Path | None = None,
) -> dict:
    """Attach signed, replayable provenance to a /prove-it artifact. ADDITIVE + FAIL-OPEN.

    ``spec``  — the prove-it citations spec dict (title, sources, citations).
    ``prov``  — the provenance dict returned by render() (grounding_rate, tally, …).

    Returns ``{ok, grounding_rate, idr_addr, memory_head, reason}``. NEVER raises.
    ``path`` / ``idr_path`` / ``head_pointer`` point the record at a separate chain:
    a private one (``legal_receipt --chain-dir``) or a test's hermetic ledgers.
    """
    try:
        return _record(spec, prov, path=path, idr_path=idr_path, head_pointer=head_pointer)
    except Exception as exc:  # noqa: BLE001 — fail-open is the contract
        _warn(f"prove-it provenance recording degraded (artifact unaffected): {exc}")
        return {"ok": False, "grounding_rate": 0.0, "idr_addr": "",
                "memory_head": "", "reason": f"error: {type(exc).__name__}"}


def _record(spec: dict, prov: dict, *, path: Any, idr_path: Any, head_pointer: Any) -> dict:
    """Inner record path (wrapped by record_proveit_provenance's fail-open guard)."""
    from dataclasses import asdict
    import lib.precog.idr as _idr
    import lib.context_chain as _cc

    fp = _fingerprint(spec)
    decision = _idr_decision(spec, prov)

    leaf = _idr.build_idr(
        prompt="", fingerprint=fp, decision=decision,
        predecessor_idr=None, depth=0, kind=IDR_KIND,
        inputs={"grounding_rate": prov.get("grounding_rate", 0.0)},
    )
    _idr.append_idr(leaf, path=idr_path)
    idr_addr = _idr.content_addr(asdict(leaf))

    title = spec.get("title", "")
    rate_pct = int(prov.get("grounding_rate", 0.0) * 100)
    node = _cc.checkpoint(
        next_step=None,
        summary=f"prove-it: {title} @ {rate_pct}%"[:600],
        title="prove-it provenance",
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
    print(f"[prove-it-provenance] {msg}", file=sys.stderr)
