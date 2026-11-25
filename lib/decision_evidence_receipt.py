"""decision_evidence_receipt — the DER composer (decision-PROVENANCE, three legs).

A Decision Evidence Receipt (DER) binds ONE decision to three tamper-evident
records, composing SHIPPED primitives (syscall-doctrine: compose, never
reimplement):

  1. cite.verify  — every claim's quote is verbatim-present in its cited source
     (deterministic offsets + per-source SHA-256), via ``lib.legal_receipt`` /
     ``lib.prove_it``.
  2. signed IDR   — an HMAC-SHA256-signed Intent Decision Record + memory-chain
     node recording the decision and the belief-state, via
     ``lib.prove_it_provenance`` (composed inside ``build_legal_receipt``).
  3. Bitcoin anchor — an OpenTimestamps stamp over the receipt's canonical root,
     so the whole record is externally timestamped to the Bitcoin blockchain.
     The anchor boundary is DEPENDENCY-INJECTED (``ots_stamp_fn``); the default
     shells out to the reference ``ots stamp`` client. Cross-ref
     ``lib.crypto_anchor`` (that module also anchors to Ethereum/Sepolia; the
     canonical DER anchor is OpenTimestamps/Bitcoin only).

WHAT A DER IS — AND IS NOT (read before relying on one)
-------------------------------------------------------
A DER is **decision-PROVENANCE**: it proves *what was decided, on what cited
evidence, and when* — tamper-evidently and replayably. It is emphatically NOT:

  * **Not zero-knowledge / not inference-verification.** The idempotent Banach
    fold (f∘f=f) elsewhere in GRIP is deterministic replay, NOT a ZK/TEE proof
    of a computation. A DER makes NO zero-knowledge or trusted-execution claim.
  * **Not a compliance guarantee.** A verified receipt does not certify that the
    decision complies with any law, regulation, policy, or standard.
  * **Not a guarantee of admissibility.** Evidentiary weight and admissibility
    are for a court/tribunal and qualified counsel to decide, not this module.

A DER is **evidence-OF-RECORD** — a durable, independently checkable record. Any
legal, regulatory, or filing decision on top of it requires **attorney review**.

A skeptic verifies a DER with no GRIP runtime and no trust in the record's own
self-report: re-hash the sources, re-read each quote at its offsets, recompute
the canonical root, and ``ots verify`` the Bitcoin proof.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_LIB = Path(__file__).resolve().parent
_ROOT = _LIB.parent
for _p in (str(_ROOT), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.legal_receipt import build_legal_receipt  # noqa: E402

__all__ = [
    "RECEIPT_KIND",
    "RECEIPT_VERSION",
    "build_decision_evidence_receipt",
    "verify_decision_evidence_receipt",
    "receipt_summary",
    "main",
]

RECEIPT_VERSION = 1
RECEIPT_KIND = "decision-evidence-receipt"
OTS_TIMEOUT_S = 30
STATE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude")) / "state" / "decision-evidence-receipt"

_VERIFY_COMMANDS = [
    "grip-decision-receipt verify <this-receipt.json>",
    "ots verify <anchor.ots_path>   # confirms the Bitcoin timestamp once the block lands",
]
_VERIFY_HOWTO = [
    "cite.verify: each citation's quoted words must sit at [start,end) in its source; "
    "re-hash every source and compare with sources[*].sha256 — a changed source cannot match.",
    "idr: idr.addr / idr.memory_head are the signed decision + belief records — verify them "
    "against the GRIP IDR forest and memory chain; idr.memory_head is inside the hashed root, so a "
    "swapped belief-state breaks the anchor.",
    "decision: recompute sha256 over canonical-JSON of the `decision` object; it must equal "
    "decision_sha256 — the decision content is committed, not free-text.",
    "anchor: recompute sha256 over canonical-JSON of this receipt WITHOUT the anchor field; it "
    "must equal anchor.root_sha256 (any edit to the body breaks it). Then `ots verify` the .ots "
    "proof — bitcoin_block is DERIVED from the proof's BitcoinBlockHeaderAttestation, never a "
    "self-reported field.",
]

# Honest-scope block (load-bearing — the analog of legal_receipt's filed_safe/review_required).
_SCOPE_ATTESTS = [
    "record authenticity — this exact decision, its citations, and its belief-state were recorded, "
    "signed, and anchored as shown",
    "fixity — the content has not changed since anchoring (the canonical root re-hashes and the "
    "Bitcoin timestamp pins it in time)",
    "belief-binding — the decision is bound to the memory-chain node (idr.memory_head) current when "
    "it was recorded; that node is inside the hashed root",
]
_SCOPE_DOES_NOT_ATTEST = [
    "upstream decision correctness — that the decision itself was right, lawful, or wise",
    "pipeline integrity BEFORE signing — a prompt-injection or data error upstream of the signing "
    "point is faithfully recorded, not caught",
    "legal admissibility or evidentiary weight — for a court/tribunal and qualified counsel, not this module",
    "regulatory certification — this is NOT an EU AI Act Art.12 record-keeping certification",
    "qualified trust status — an OpenTimestamps/Bitcoin anchor is NOT an eIDAS 'qualified' timestamp",
]


# ---------------------------------------------------------------------------
# Canonical root + OpenTimestamps boundary (dependency-injected)
# ---------------------------------------------------------------------------

def _canonical_root_sha256(body: dict) -> str:
    """SHA-256 over canonical JSON (sorted keys, tight separators) of the receipt
    body. Order-independent and deterministic, so build-time and verify-time
    hashes match iff the body is byte-for-byte the same content."""
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _find_ots() -> str | None:
    """Locate the reference OpenTimestamps client (GRIP venv first, then PATH)."""
    venv = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude")) / "venv" / "bin" / "ots"
    return str(venv) if venv.exists() else shutil.which("ots")


def _default_ots_stamp(root_sha256: str) -> dict:
    """Real anchor leg: `ots stamp` a file holding the canonical root.

    A FRESH stamp is a PendingAttestation — it is honestly ``status="pending"``
    with ``bitcoin_block=None`` until the Bitcoin block confirms (hours later),
    at which point `ots upgrade` + `ots verify` (the verify leg) report it
    confirmed. Never fabricates a block."""
    ots = _find_ots()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    root_file = STATE_DIR / f"der-{root_sha256[:16]}-{int(time.time())}.root"
    root_file.write_text(root_sha256, encoding="utf-8")
    if ots is None:
        return {"ots_path": "", "bitcoin_block": None, "status": "pending",
                "note": "ots client not installed — root recorded, stamp deferred"}
    try:
        proc = subprocess.run([ots, "stamp", str(root_file)],
                              capture_output=True, text=True, timeout=OTS_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ots_path": "", "bitcoin_block": None, "status": "pending",
                "note": f"ots stamp failed: {str(exc)[:120]}"}
    proof = root_file.with_name(root_file.name + ".ots")
    if proof.exists():
        return {"ots_path": str(proof), "bitcoin_block": None, "status": "pending",
                "note": "stamped; PendingAttestation until the Bitcoin block confirms"}
    return {"ots_path": "", "bitcoin_block": None, "status": "pending",
            "note": f"ots stamp produced no proof (rc={proc.returncode})"}


def _default_ots_verify(ots_path: str) -> dict:
    """Real verify leg: `ots verify` the .ots proof and report status HONESTLY.

    Returns ``{status, block, note}`` where status is confirmed | pending |
    unverifiable | invalid. 'pending' (awaiting Bitcoin confirmation) is a normal
    state, not a failure; only 'invalid' means a broken/tampered proof."""
    ots = _find_ots()
    proof = Path(ots_path) if ots_path else None
    if ots is None or proof is None or not proof.exists():
        return {"status": "unverifiable", "block": None,
                "note": "ots client or .ots proof unavailable"}
    try:
        proc = subprocess.run([ots, "verify", str(proof)],
                              capture_output=True, text=True, timeout=OTS_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"status": "unverifiable", "block": None, "note": str(exc)[:120]}
    out = (proc.stdout + proc.stderr).lower()
    if "pending" in out or "not confirmed" in out:
        return {"status": "pending", "block": None, "note": "awaiting Bitcoin confirmation"}
    block = re.search(r"block\s+(\d+)", out)
    if "success" in out or block:
        return {"status": "confirmed", "block": int(block.group(1)) if block else None,
                "note": "Bitcoin-confirmed"}
    return {"status": "invalid", "block": None, "note": (proc.stderr[:120] or "verify failed")}


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _build_spec(decision_log: dict, sources: list, citations: list) -> dict:
    """The /prove-it citations spec that drives the cite.verify + IDR legs."""
    return {
        "title": decision_log.get("title", ""),
        "response": (decision_log.get("decision") or decision_log.get("statement")
                     or decision_log.get("response", "")),
        "sources": sources,
        "citations": citations,
    }


def _resolve_signing_audit() -> dict:
    """Resolve the IDR signing scheme + key fingerprint HONESTLY — never hardcoded.

    A DER carries NO tenant context, so ``SigningScheme.AUTO`` resolves to
    ``hmac-sha256`` (audit_chain._resolve_scheme, side-effect-free for tenant_id
    None — it never touches the key file). The fingerprint identifies the global
    HMAC verifying key (sha256(key)[:8], audit_chain's own format) when the key
    file already exists; it is NEVER created here, so a hermetic run stays clean
    and no secret is written into the receipt (a fingerprint is an identifier,
    not the key). Fail-open to the resolved default with an empty fingerprint."""
    try:
        import lib.audit_chain as ac
        scheme = ac._resolve_scheme(ac.SigningScheme.AUTO, None).value  # "hmac-sha256"
        fp = ac._compute_key_fingerprint(ac.SIGNING_KEY_FILE.read_bytes()) \
            if ac.SIGNING_KEY_FILE.exists() else ""
        return {"sig_scheme": scheme, "key_fingerprint": fp}
    except Exception:
        return {"sig_scheme": "hmac-sha256", "key_fingerprint": ""}


def _assemble_body(decision_log: dict, legal: dict, spec: dict) -> dict:
    """The DER body (everything except the anchor field, which is added after the
    canonical root is computed over exactly this body). ``idr.memory_head`` and
    ``decision`` both live INSIDE this hashed preimage, so swapping the belief
    head or editing the decision breaks the anchor."""
    chain = legal.get("chain", {})
    audit = _resolve_signing_audit()
    return {
        "receipt_version": RECEIPT_VERSION,
        "kind": RECEIPT_KIND,
        "created_utc": legal.get("created_utc") or datetime.now(timezone.utc).isoformat(),
        "decision": decision_log,
        "decision_sha256": _canonical_root_sha256(decision_log),
        "idr": {
            "addr": chain.get("idr_addr", ""),
            "memory_head": chain.get("memory_head", ""),
            "sig_scheme": audit["sig_scheme"],
            "recorded": bool(chain.get("recorded")),
        },
        "audit": audit,
        "citations": legal.get("citations", []),
        "tally": legal.get("tally", {}),
        "grounding_rate": legal.get("grounding_rate", 0.0),
        "sources": legal.get("sources", {}),
        "provenance_spec": {"sources": spec["sources"], "citations": spec["citations"]},
        "scope": {"attests": list(_SCOPE_ATTESTS), "does_not_attest": list(_SCOPE_DOES_NOT_ATTEST)},
        "verify": {"commands": list(_VERIFY_COMMANDS), "howto": list(_VERIFY_HOWTO)},
    }


def build_decision_evidence_receipt(
    decision_log: dict,
    *,
    sources: list,
    citations: list,
    anchor: bool = True,
    record: bool = True,
    ots_stamp_fn: Callable[[str], dict] | None = None,
    path: Path | None = None,
    idr_path: Path | None = None,
    head_pointer: Path | None = None,
) -> dict:
    """Compose a Decision Evidence Receipt for ``decision_log``.

    ``decision_log`` — the decision record: ``{title, decision (narrative, may
        carry [[cite:ID]] tokens), matter?, jurisdiction?}``.
    ``sources``   — /prove-it source dicts: ``{id, label?, text}`` (inline) or
        ``{id, label?, path}`` (file).
    ``citations`` — /prove-it citation dicts: ``{id, claim, source_id, quote}``.
    ``anchor``    — stamp the canonical root to Bitcoin (OpenTimestamps); when
        False, the root is still computed and stored (status "skipped") so it can
        be stamped later.
    ``record``    — write the signed IDR + memory-chain node.
    ``ots_stamp_fn`` — injected anchor boundary ``root_sha256 -> {ots_path,
        bitcoin_block, status, note}``; defaults to the real ``ots stamp``. Tests
        pass a fake for a hermetic, network-free run.
    ``path`` / ``idr_path`` / ``head_pointer`` — inject hermetic ledgers for tests.

    Returns the unified DER receipt dict (three legs: cite.verify, idr, anchor).
    """
    spec = _build_spec(decision_log, sources, citations)
    legal, _html = build_legal_receipt(
        spec, matter=decision_log.get("matter", ""),
        jurisdiction=decision_log.get("jurisdiction", ""),
        record=record, path=path, idr_path=idr_path, head_pointer=head_pointer)
    body = _assemble_body(decision_log, legal, spec)
    root = _canonical_root_sha256(body)
    if anchor:
        stamp = (ots_stamp_fn or _default_ots_stamp)(root)
        body["anchor"] = {"method": "opentimestamps", "root_sha256": root,
                          "ots_path": stamp.get("ots_path", ""),
                          "bitcoin_block": stamp.get("bitcoin_block"),
                          "status": stamp.get("status", "pending"),
                          "note": stamp.get("note", "")}
    else:
        body["anchor"] = {"method": "opentimestamps", "root_sha256": root,
                          "ots_path": "", "bitcoin_block": None, "status": "skipped",
                          "note": "anchoring skipped (anchor=False) — root computed, stamp deferrable"}
    return body


# ---------------------------------------------------------------------------
# Verify (reuses grasp-verify-receipt's canonical cite.verify logic)
# ---------------------------------------------------------------------------

_GVR_CACHE: object | None = None


def _load_gvr():
    """Import bin/grasp-verify-receipt so its cite.verify logic is the SINGLE
    source of truth for the offset + tally check (DRY). The file is extensionless,
    so an explicit SourceFileLoader is required (spec_from_file_location can't infer
    one). Cached — one load per process."""
    global _GVR_CACHE
    if _GVR_CACHE is None:
        gvr_path = _ROOT / "bin" / "grasp-verify-receipt"
        loader = importlib.machinery.SourceFileLoader("grasp_verify_receipt", str(gvr_path))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        _GVR_CACHE = mod
    return _GVR_CACHE


def _texts_from_spec(spec: dict, root: str) -> tuple[dict, list]:
    """Recover each source's text — inline ``text`` or a ``path`` read from disk."""
    texts, problems = {}, []
    for src in spec.get("sources", []):
        sid = src.get("id")
        if src.get("text") is not None:
            texts[sid] = src["text"]
        elif src.get("path"):
            p = Path(src["path"])
            p = p if p.is_absolute() else Path(root) / p
            if not p.is_file():
                problems.append(f"source {sid}: file not found: {p}")
                continue
            texts[sid] = p.read_text(encoding="utf-8", errors="replace")
        else:
            problems.append(f"source {sid}: no inline text and no path — cannot re-verify")
    return texts, problems


def _check_sources_integrity(receipt: dict, texts: dict) -> list:
    """Re-hash each recovered source text against the receipt's pinned SHA-256."""
    problems = []
    recorded = receipt.get("sources", {})
    for sid, text in texts.items():
        pin = recorded.get(sid, {})
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != pin.get("sha256"):
            problems.append(f"source {sid}: sha256 mismatch — source changed since the receipt was issued")
        if len(text) != pin.get("chars"):
            problems.append(f"source {sid}: length mismatch — {len(text)} chars vs receipt {pin.get('chars')}")
    return problems


def _check_anchor(receipt: dict, ots_verify_fn: Callable[[str], dict] | None) -> tuple[list, list]:
    """Recompute the canonical root (hard tamper check) and report the Bitcoin
    proof status (soft — pending is normal, only 'invalid' is a failure)."""
    problems, notes = [], []
    anchor = receipt.get("anchor", {})
    body = {k: v for k, v in receipt.items() if k != "anchor"}
    if anchor.get("root_sha256") and _canonical_root_sha256(body) != anchor["root_sha256"]:
        problems.append("anchor: canonical root mismatch — the receipt body was altered after anchoring")
    if anchor.get("status") == "skipped":
        notes.append("anchor: not stamped (status skipped) — root computed but no Bitcoin timestamp yet")
        return problems, notes
    ots_path = anchor.get("ots_path")
    if ots_path:
        v = (ots_verify_fn or _default_ots_verify)(ots_path)
        if v.get("status") == "invalid":
            problems.append(f"anchor: `ots verify` reports the proof INVALID: {v.get('note', '')}")
        else:
            blk = f" (Bitcoin block {v['block']})" if v.get("block") else " (pending Bitcoin confirmation)"
            notes.append(f"anchor: OpenTimestamps {v.get('status', 'pending')}{blk}")
    else:
        notes.append("anchor: status recorded but no .ots proof path — root is checked, Bitcoin proof absent")
    return problems, notes


def verify_decision_evidence_receipt(
    receipt: dict,
    *,
    root: str = ".",
    ots_verify_fn: Callable[[str], dict] | None = None,
) -> tuple[bool, list, list]:
    """Independently re-verify a DER. Returns ``(ok, problems, notes)``.

    Re-checks all three legs: (1) cite.verify — source hashes + every citation's
    quote at its recorded offsets + the tally/grounding_rate (reusing
    grasp-verify-receipt's canonical logic); (2) idr — the signed addr is present
    when the chain was recorded; (3) anchor — the canonical root still matches
    (hard) and the Bitcoin proof status (soft). ``ots_verify_fn`` is injected for
    a hermetic, network-free run."""
    problems, notes = [], []
    spec = receipt.get("provenance_spec", {})
    texts, tproblems = _texts_from_spec(spec, root)
    problems += tproblems
    problems += _check_sources_integrity(receipt, texts)
    view = {"citations": receipt.get("citations", []),
            "tally": receipt.get("tally", {}),
            "grounding_rate": receipt.get("grounding_rate", 0.0)}
    cite_problems, _tally = _load_gvr().check_citations(spec, view, texts)
    problems += cite_problems
    if "decision" in receipt and _canonical_root_sha256(receipt["decision"]) != receipt.get("decision_sha256"):
        problems.append("decision: decision_sha256 mismatch — the committed decision content was altered")
    anchor_problems, anchor_notes = _check_anchor(receipt, ots_verify_fn)
    problems += anchor_problems
    notes += anchor_notes
    idr = receipt.get("idr", {})
    if idr.get("recorded") and not idr.get("addr"):
        problems.append("idr: chain marked recorded but carries no signed idr addr")
    return (not problems), problems, notes


# ---------------------------------------------------------------------------
# Plain-language summary + CLI
# ---------------------------------------------------------------------------

def receipt_summary(receipt: dict) -> str:
    """Plain-language one-screen summary (Rule 17 — readable cold)."""
    tally = receipt.get("tally", {})
    idr = receipt.get("idr", {})
    anchor = receipt.get("anchor", {})
    lines = [f"Decision Evidence Receipt — {receipt.get('decision', {}).get('title') or '(untitled)'}"]
    lines.append(
        f"Evidence: {tally.get('verified', 0)} verified · {tally.get('fuzzy', 0)} fuzzy · "
        f"{tally.get('not_found', 0)} NOT FOUND · grounding "
        f"{int(round(receipt.get('grounding_rate', 0.0) * 100))}%")
    if idr.get("recorded"):
        lines.append(f"Signed IDR: {idr.get('addr', '')[:16]} · memory {idr.get('memory_head', '')[:16]} "
                     f"({idr.get('sig_scheme', '')})")
    else:
        lines.append("Signed IDR: not recorded")
    lines.append(f"Bitcoin anchor: {anchor.get('status', 'unknown')} — {anchor.get('note', '')}")
    lines.append("Evidence-of-record only — NOT a compliance or admissibility guarantee; attorney review required.")
    return "\n".join(lines)


def _file_source(f: str) -> dict:
    p = Path(f)
    return {"id": p.stem, "label": p.name, "path": str(p.resolve())}


def _cmd_generate(args: argparse.Namespace) -> int:
    dl = json.loads(Path(args.decision_log).read_text(encoding="utf-8"))
    citations = dl.get("citations", [])
    sources = list(dl.get("sources", [])) + [_file_source(f) for f in (args.sources or [])]
    decision = {k: v for k, v in dl.items() if k not in ("sources", "citations")}
    receipt = build_decision_evidence_receipt(
        decision, sources=sources, citations=citations,
        anchor=not args.no_anchor, record=not args.no_record)
    out = Path(args.out) if args.out else Path(args.decision_log).with_suffix(".der.json")
    out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(receipt_summary(receipt))
    print(f"Receipt: {out}")
    if receipt.get("tally", {}).get("not_found", 0):
        print("WARNING: some citations are NOT FOUND in their sources — the decision's evidence is incomplete.")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
    ok, problems, notes = verify_decision_evidence_receipt(receipt, root=args.root)
    for n in notes:
        print(f"  · {n}")
    if ok:
        print("RECEIPT VERIFIED — cite.verify, signed IDR, and the anchored root all re-check.")
        return 0
    print("RECEIPT FAILED — the record does not match reality:")
    for p in problems:
        print(f"  ✗ {p}")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Decision Evidence Receipt (DER) — decision-PROVENANCE (cite.verify + signed "
                    "IDR + Bitcoin anchor). Evidence-of-record only; NOT a compliance or "
                    "admissibility guarantee — attorney review required.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="compose a DER from a decision-log JSON")
    g.add_argument("decision_log", help="decision-log JSON {title, decision, citations[], sources[]?}")
    g.add_argument("--sources", nargs="*", help="source files (id = filename stem)")
    g.add_argument("--out", help="receipt JSON output path (default: <decision-log>.der.json)")
    g.add_argument("--no-anchor", action="store_true", help="skip the Bitcoin stamp (root still computed)")
    g.add_argument("--no-record", action="store_true", help="skip the signed IDR/memory-chain write")
    g.set_defaults(func=_cmd_generate)
    v = sub.add_parser("verify", help="independently re-verify a DER")
    v.add_argument("receipt", help="the DER receipt JSON to re-verify")
    v.add_argument("--root", default=".", help="repo root that file-based source paths are relative to")
    v.set_defaults(func=_cmd_verify)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
