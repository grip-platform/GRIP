"""legal_receipt — GRASP receipt for a legal deliverable (the /legal-ops INFRA floor).

GRASP = Governed Reasoning And Signable Provenance. A legal deliverable (memo,
opinion, contract analysis) is SAFE TO FILE only when every quote it relies on
is provably present, verbatim, in its cited source — and the check itself is
recorded tamper-evidently. This module composes three SHIPPED primitives
(syscall-doctrine: compose, never reimplement):

  1. ``lib.prove_it.render`` — the deterministic L1 citation floor
     (verified / fuzzy / not_found + per-source SHA-256 + offsets).
  2. ``lib.prove_it_provenance.record_proveit_provenance`` — signed IDR leaf +
     memory-chain node (fail-open, additive — never blocks the receipt).
  3. A SHA-256 fingerprint over the deliverable file itself.

The receipt is ONE self-contained JSON a skeptic can check without GRIP:
re-hash the sources, re-read each quote at its recorded offsets, re-verify the
signed chains. "Never file a red" is mechanical here: ``filed_safe`` is False —
and the CLI exits 1 — whenever any citation is not_found, or when there are no
citations at all (an unchecked deliverable is not a checked one).

CLI:
    python3 -m lib.legal_receipt spec.json \
        [--deliverable FILE] [--matter REF] [--jurisdiction J] \
        [--out receipt.json] [--html artifact.html] [--no-record | --chain-dir DIR]

``--chain-dir DIR`` signs the receipt onto a private chain in DIR instead of the
shared one, for a matter whose record must stay out of the shared chain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent
_ROOT = _LIB.parent
for _p in (str(_ROOT), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.prove_it import STATUS_FUZZY, STATUS_NOT_FOUND, STATUS_VERIFIED, render  # noqa: E402
from lib.prove_it_provenance import record_proveit_provenance  # noqa: E402

__all__ = ["RECEIPT_KIND", "RECEIPT_VERSION", "build_legal_receipt", "receipt_summary", "main"]

RECEIPT_VERSION = 1
RECEIPT_KIND = "grasp-legal-receipt"

_VERIFY_HOWTO = [
    "Re-hash each source text (SHA-256) and compare with sources[*].sha256 — a changed source cannot match.",
    "For each citation, read the source at [start, end) — the quoted words must be there verbatim (fuzzy tolerates whitespace/typography only, never words).",
    "Any citation with status not_found is a quote its cited source does not contain — the deliverable must not be filed on it.",
    "chain.idr_addr / chain.memory_head are the signed decision + belief records; verify them against the GRIP IDR forest and memory chain.",
    "chain.ledger says where those records are: 'shared' is GRIP's own chain, 'own' is a chain folder the issuer kept (the CLI's --chain-dir), 'none' means nothing was signed.",
]


def _ledger_label(record: bool, idr_path: Path | None) -> str:
    """Where the signed records went: 'shared', 'own' (caller-given paths) or 'none'.

    A verifier who looks for an 'own' record in the shared forest finds nothing,
    and could read a valid receipt as forged. The label tells them where to look.
    """
    if not record:
        return "none"
    return "own" if idr_path is not None else "shared"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _filed_safety(prov: dict) -> tuple[bool, str]:
    """Mechanical never-file-a-red gate. Returns (safe, plain-language reason)."""
    n_cites = len(prov.get("citations", []))
    if n_cites == 0:
        return False, "no citations were checked — an unchecked deliverable is not a checked one"
    reds = prov.get("tally", {}).get(STATUS_NOT_FOUND, 0)
    if reds:
        return False, f"{reds} citation(s) NOT FOUND in their cited sources — never file a red"
    return True, f"all {n_cites} citation(s) verbatim-present in their cited sources"


def _l2_advisory(cid: str, verdict: str, providers: int) -> str | None:
    """Plain-language attorney-review flag for an L2 verdict (None if clean)."""
    if verdict == "unsupported":
        return (f"{cid}: verbatim-present quote that {providers} external model(s) judged "
                f"does NOT support its claim — a real citation used for a proposition it "
                f"does not bear; attorney review required before relying.")
    if verdict == "partial":
        return (f"{cid}: verbatim-present quote whose support for its claim the external "
                f"panel judged PARTIAL — attorney review recommended.")
    return None


def _run_council(spec: dict, prov: dict, panel=None, judge_fn=None) -> tuple[dict, list]:
    """L2 — external multi-LLM council leg: for each citation the L1 floor
    LOCATED (verified/fuzzy), ask a panel of external models whether the quote
    actually SUPPORTS its claim, with an independent judge.

    MONOTONICITY (llm-augmentation-pattern): this is advisory and toward-doubt.
    It can flag a real, verbatim-present quote as UNSUPPORTED for attorney
    review — a citation can be 100% genuine and still not bear the proposition
    it is cited for — but it NEVER overrides the L1 filing gate, and a
    not_found quote is never reviewed (the floor already refused it).
    Fail-open: unreachable providers ⇒ verdict "unavailable", receipt still
    files on L1.
    """
    try:
        from lib.prove_it_council import council_review
    except Exception:
        return {"ran": False, "reason": "council module unavailable"}, []
    status_by_id = {c.get("id"): c.get("status") for c in prov.get("citations", [])}
    reviewed, unsupported, partial, advisory = [], [], [], []
    for cit in spec.get("citations", []):
        cid = cit.get("id")
        if status_by_id.get(cid) not in (STATUS_VERIFIED, STATUS_FUZZY):
            continue  # monotonicity: only L1-located quotes are reviewed
        r = council_review(cit.get("claim", ""), cit.get("quote", ""),
                           panel=panel, judge_fn=judge_fn)
        verdict = r.get("verdict", "unavailable")
        reviewed.append({"id": cid, "support": verdict,
                         "confidence": r.get("confidence", 0.0),
                         "providers": r.get("providers", 0), "votes": r.get("votes", {})})
        if verdict == "unsupported":
            unsupported.append(cid)
        elif verdict == "partial":
            partial.append(cid)
        line = _l2_advisory(cid, verdict, r.get("providers", 0))
        if line:
            advisory.append(line)
    block = {
        "ran": True,
        "reviewed": len(reviewed),
        "unsupported": unsupported,
        "partial": partial,
        "verdicts": reviewed,
        "note": ("L2 is advisory and monotone toward doubt: it flags a real quote as "
                 "unsupported but never overrides the L1 filing gate."),
    }
    return block, advisory


def build_legal_receipt(
    spec: dict,
    *,
    deliverable_path: Path | str | None = None,
    matter: str = "",
    jurisdiction: str = "",
    record: bool = True,
    council: bool = False,
    panel=None,
    judge_fn=None,
    path: Path | None = None,
    idr_path: Path | None = None,
    head_pointer: Path | None = None,
) -> tuple[dict, str]:
    """Build the GRASP receipt for a legal deliverable. Returns (receipt, html).

    ``spec`` is a /prove-it citations spec (title, response, sources, citations).
    ``record=False`` skips the signed-chain write (previews); the receipt then
    carries chain.recorded=False with reason "recording disabled".
    ``council=True`` runs the L2 external-model relevance leg (advisory, never
    gates filing); ``panel`` / ``judge_fn`` are injected for hermetic tests.
    ``path`` / ``idr_path`` / ``head_pointer`` point the record at a separate chain:
    a private one (the CLI's ``--chain-dir``) or a test's hermetic ledgers.
    """
    html, prov = render(spec)

    deliverable = None
    if deliverable_path is not None:
        p = Path(deliverable_path)
        deliverable = {"path": str(p), "sha256": _sha256_file(p), "bytes": p.stat().st_size}

    if record:
        chain = record_proveit_provenance(
            spec, prov, path=path, idr_path=idr_path, head_pointer=head_pointer)
    else:
        chain = {"ok": False, "idr_addr": "", "memory_head": "", "reason": "recording disabled"}

    filed_safe, reason = _filed_safety(prov)
    council_block, advisory = ({"ran": False}, [])
    if council:
        council_block, advisory = _run_council(spec, prov, panel=panel, judge_fn=judge_fn)
    receipt = {
        "receipt_version": RECEIPT_VERSION,
        "kind": RECEIPT_KIND,
        "title": spec.get("title", ""),
        "matter": matter,
        "jurisdiction": jurisdiction,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "deliverable": deliverable,
        "grounding_rate": prov.get("grounding_rate", 0.0),
        "tally": prov.get("tally", {}),
        "sources": prov.get("sources", {}),
        "citations": prov.get("citations", []),
        "chain": {
            "recorded": bool(chain.get("ok")),
            "idr_addr": chain.get("idr_addr", ""),
            "memory_head": chain.get("memory_head", ""),
            "reason": chain.get("reason", ""),
            "ledger": _ledger_label(record, idr_path),
        },
        "filed_safe": filed_safe,
        "filed_safe_reason": reason,
        "council": council_block,
        "advisory": advisory,
        "review_required": bool(advisory),
        "verify_howto": _VERIFY_HOWTO,
    }
    return receipt, html


def receipt_summary(receipt: dict) -> str:
    """Plain-language summary (Rule 17 — readable by a first-time reader)."""
    tally = receipt.get("tally", {})
    reds = tally.get(STATUS_NOT_FOUND, 0)
    verdict = "SAFE TO FILE" if receipt.get("filed_safe") else "DO NOT FILE"
    lines = [f"GRASP legal receipt — {receipt.get('title') or '(untitled)'}"]
    ctx_parts = []
    if receipt.get("matter"):
        ctx_parts.append(f"Matter: {receipt['matter']}")
    if receipt.get("jurisdiction"):
        ctx_parts.append(f"Jurisdiction: {receipt['jurisdiction']}")
    if ctx_parts:
        lines.append(" · ".join(ctx_parts))
    lines.append(
        f"Citations: {tally.get(STATUS_VERIFIED, 0)} verified · "
        f"{tally.get(STATUS_FUZZY, 0)} fuzzy · {reds} NOT FOUND")
    lines.append(f"Grounding: {int(round(receipt.get('grounding_rate', 0.0) * 100))}%")
    chain = receipt.get("chain", {})
    if chain.get("recorded"):
        # Name the ledger, so a run on an own chain never reads as a shared one.
        # A receipt from before the field existed carries no ledger, and says none.
        ledger = f" · ledger {chain['ledger']}" if chain.get("ledger") else ""
        lines.append(
            f"Signed chain: IDR {chain.get('idr_addr', '')[:16]} · "
            f"memory head {chain.get('memory_head', '')[:16]}{ledger}")
    else:
        lines.append(f"Signed chain: not recorded ({chain.get('reason', 'unknown')})")
    lines.append(f"Verdict: {verdict} — {receipt.get('filed_safe_reason', '')}")
    council = receipt.get("council", {})
    if council.get("ran"):
        lines.append(
            f"External council (L2): {council.get('reviewed', 0)} reviewed · "
            f"{len(council.get('unsupported', []))} unsupported · "
            f"{len(council.get('partial', []))} partial")
        if receipt.get("review_required"):
            lines.append("⚠ Attorney review required (real quote, unsupported claim):")
            for a in receipt.get("advisory", []):
                lines.append(f"  - {a}")
    return "\n".join(lines)


def _chain_paths(chain_dir: str | None) -> dict:
    """Ledger paths for a private chain in ``chain_dir``; an empty dict means the shared chain.

    A matter that must stay out of the shared chain (a personal or a client matter)
    signs its receipt onto a chain of its own: the decision record, the memory node
    and the head pointer all land in ``chain_dir``. The shared chain's head does not
    move, because ``lib.precog.idr._advance_head`` leaves a custom ledger alone.
    """
    if not chain_dir:
        return {}
    root = Path(chain_dir)
    root.mkdir(parents=True, exist_ok=True)
    return {"path": root / "context.jsonl", "idr_path": root / "idr.jsonl",
            "head_pointer": root / "HEAD"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a GRASP receipt for a legal deliverable.")
    ap.add_argument("spec", help="path to a /prove-it citations spec JSON")
    ap.add_argument("--deliverable", help="deliverable file to fingerprint (memo, opinion, contract analysis)")
    ap.add_argument("--matter", default="", help="matter reference")
    ap.add_argument("--jurisdiction", default="", help="governing jurisdiction")
    ap.add_argument("--out", help="receipt JSON output path (default: <spec>.receipt.json)")
    ap.add_argument("--html", help="also write the clickable /prove-it artifact here")
    chain = ap.add_mutually_exclusive_group()
    chain.add_argument("--no-record", action="store_true",
                       help="skip the signed IDR/memory-chain write (preview only)")
    chain.add_argument("--chain-dir", metavar="DIR",
                       help="sign onto a private chain in DIR (idr.jsonl, context.jsonl, "
                            "HEAD) instead of the shared chain")
    ap.add_argument("--council", action="store_true",
                    help="run the L2 external-model relevance leg (advisory; flags "
                         "real quotes that do not support their claim)")
    args = ap.parse_args(argv)
    if args.chain_dir is not None and not args.chain_dir.strip():
        # An unset shell variable arrives as "". Falling back to the shared chain
        # would sign a private record onto the very chain the caller avoided.
        ap.error("--chain-dir needs a folder; an empty value would sign onto the shared chain")

    spec_path = Path(args.spec)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    receipt, html = build_legal_receipt(
        spec,
        deliverable_path=args.deliverable,
        matter=args.matter,
        jurisdiction=args.jurisdiction,
        record=not args.no_record,
        council=args.council,
        **_chain_paths(args.chain_dir),
    )
    out = Path(args.out) if args.out else spec_path.with_suffix(".receipt.json")
    out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.html:
        Path(args.html).write_text(html, encoding="utf-8")
    print(receipt_summary(receipt))
    print(f"Receipt: {out}")
    return 0 if receipt["filed_safe"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
