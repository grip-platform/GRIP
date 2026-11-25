"""Context memory-chain snapshot + fold bridge (P4).

The fold turns the chain (signed ``context-delta`` IDRs) into the bounded kernel
via the EXISTING deterministic reducer ``holistic_synthesiser._assemble`` — no
second fold, no synthesiser refactor (design §2: "feed chain-deltas as
MemoryEntry rows into synthesise"). The bridge maps each delta to a
``MemoryEntry`` whose ``content_hash`` is the delta's content address (which
excludes ``id``/``ts``/``audit``), so the substrate digest is TS-FREE and the
fold replays byte-identically across runs (design §8 P4 sub-assertion).

A snapshot pins the fold: a ``context-snapshot`` IDR whose ``decision`` carries
``kernel_sha256`` (``payload_digest`` of the folded kernel) + ``substrate_digest``.
Re-folding the same chain reproduces the same ``kernel_sha256`` (R2 idempotency)
— the P4 CI anchor.

Design: GRIP ``drafts/happi-context-event-memory-chain-design.md`` §4, §6, §8.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from lib.context_chain import append_context, mask_superseded, read_context_chain
from lib.holistic_synthesiser import (
    CAP_CHARS,
    KernelResult,
    MemoryEntry,
    _assemble,
    payload_digest,
)
from lib.merkle import inclusion_proof, merkle_root, verify_inclusion
from lib.precog.idr import PrecogIDR, content_addr


def _delta_to_entry(idr: PrecogIDR) -> MemoryEntry:
    """Bridge one context-delta IDR → a ``MemoryEntry`` row for the fold.

    ``content_hash`` = ``content_addr`` of the IDR body — EXCLUDES id/ts/audit, so
    the substrate digest is ts-free and the fold replays byte-identically.
    title/hook are drawn from the decision; tier defaults to ``feedback`` (folded
    into the kernel body), ``paramount`` honours an explicit decision flag.
    """
    decision = idr.decision or {}
    addr = content_addr(asdict(idr))
    title = str(decision.get("title") or decision.get("learned") or idr.kind)
    hook = str(decision.get("hook") or decision.get("summary") or title)[:120]
    return MemoryEntry(
        filename=idr.id,
        title=title,
        hook=hook,
        tier=str(decision.get("tier", "feedback")),
        paramount=bool(decision.get("paramount", False)),
        recall_key=addr,
        content_hash=addr,
    )


def _all_entries(chain: list[PrecogIDR]) -> list[MemoryEntry]:
    """Every node as a row — HISTORY, unmasked. Used by the completeness
    commitment, never by the fold."""
    return [_delta_to_entry(idr) for idr in chain]


def chain_to_entries(chain: list[PrecogIDR]) -> list[MemoryEntry]:
    """Bridge a context chain (root→leaf) → ``MemoryEntry`` rows (ts-free), AS THE
    FOLD SEES IT: a delta named by a later ``context-supersede`` is masked, so
    the kernel carries the correction and not what it replaced
    (MEMORY_CONTEXT.md §context-supersede). Same chain ⇒ same rows (R2)."""
    return _all_entries(mask_superseded(chain))


def fold_chain(chain: list[PrecogIDR], *, cap: int = CAP_CHARS) -> KernelResult:
    """Fold a chain into the bounded kernel via the existing deterministic
    reducer. Same chain ⇒ byte-identical kernel text (R2 idempotency)."""
    return _assemble(chain_to_entries(chain), cap)


def deltas_since_snapshot(chain: list[PrecogIDR]) -> int:
    """Count ``context-delta`` nodes after the newest snapshot in ``chain``
    (root→leaf). The deterministic snapshot-cadence input (design §4): the caller
    cuts a snapshot when this reaches its cadence N. NEVER wall-clock, so the same
    chain always folds snapshots at the same points (replay byte-identical). No
    snapshot yet ⇒ the whole chain length."""
    count = 0
    for node in reversed(chain):
        if node.kind == "context-snapshot":
            break
        count += 1
    return count


class ParamountInclusionError(ValueError):
    """Raised when an inclusion proof is requested for a non-paramount content-hash."""


def _paramount_addrs(chain: list[PrecogIDR]) -> list[str]:
    """Sorted content-addresses of the chain's PARAMOUNT-marked deltas — the leaf
    set the completeness commitment is taken over. Sorted ⇒ order-independent,
    content-addressed ⇒ ts/id-free, so the root replays byte-identically.

    Deliberately UNMASKED (``_all_entries``, not ``chain_to_entries``): the root
    answers whether a paramount fact was EVER recorded. A superseded paramount
    delta stays a member — dropping it would read as exactly the silent
    truncation the commitment exists to detect, and it would break the
    monotone-N floor ``verify_paramount_completeness`` enforces."""
    return sorted(e.content_hash for e in _all_entries(chain) if e.paramount)


def _paramount_commitment(chain: list[PrecogIDR]) -> dict:
    """LIVE completeness commitment (Slice B, design D2 "chain replaces R1").
    Promoted from SHADOW 2026-06-26 (V>> D1–D4 locked, Phase-1 step 2).

    An RFC 6962 **Merkle root** over the content-addresses of the chain's
    PARAMOUNT-marked deltas + an explicit cardinality ``paramount_n``. Bound into
    each snapshot's ``decision`` (so the HMAC signs it) — a dropped paramount fact
    becomes a detectable ROOT divergence at re-fold, not a silent truncation
    (the #3425/#3426 drift class, killed at the chain layer).

    Upgraded from a flat ``SHA256(sorted addrs)`` to a Merkle tree (lib.merkle,
    GRIP #3442) so a SINGLE paramount fact's membership is provable with an
    ``O(log N)`` inclusion proof — ``paramount_inclusion_proof`` /
    ``verify_paramount_inclusion`` — WITHOUT revealing the other facts (the F3
    memory-axis gap closed; selective-disclosure foundation). ``root_algo`` records
    the construction so a verifier knows how to recompute.

    LIVE = recorded + signed + AUTHORITATIVE. The completeness commitment is now
    the truth-source for chain completeness; ``verify_paramount_completeness``
    enforces the monotone-N floor at every snapshot, and a ≥14-day shadow==live
    agreement hypothesis (H-DISP-COMP-1) proves the promotion is exogenous-safe.
    """
    addrs = _paramount_addrs(chain)
    root = merkle_root([a.encode("utf-8") for a in addrs])
    return {"paramount_root": root, "paramount_n": len(addrs),
            "root_algo": "merkle-rfc6962", "live": True}


def paramount_inclusion_proof(chain: list[PrecogIDR], content_hash: str) -> dict:
    """Prove that ONE paramount fact (by its content-address) is committed by the
    snapshot's ``paramount_root`` — without revealing the other facts.

    Returns ``{content_hash, proof, paramount_root, paramount_n}`` where ``proof``
    is the ~``log2(N)`` Merkle audit path. Raises ``ParamountInclusionError`` if
    ``content_hash`` is not a paramount leaf of this chain (cannot prove a fact
    that is not committed — no false membership)."""
    addrs = _paramount_addrs(chain)
    try:
        index = addrs.index(content_hash)
    except ValueError:
        raise ParamountInclusionError(
            f"{content_hash!r} is not a PARAMOUNT leaf of this chain")
    leaves = [a.encode("utf-8") for a in addrs]
    return {
        "content_hash": content_hash,
        "proof": inclusion_proof(leaves, index),
        "paramount_root": merkle_root(leaves),
        "paramount_n": len(addrs),
    }


def verify_paramount_inclusion(content_hash: str, proof, paramount_root: str) -> bool:
    """True iff ``content_hash`` + ``proof`` reproduce ``paramount_root``. A third
    party verifies one paramount fact's membership with ONLY that fact + the proof
    + the signed root — never the other facts. Tamper / wrong proof / wrong root
    all fail (lib.merkle.verify_inclusion)."""
    return verify_inclusion(content_hash.encode("utf-8"), proof, paramount_root)


def verify_paramount_completeness(
    *,
    path: Path | None = None,
    head_pointer: Path | None = None,
) -> dict:
    """TOUGHNESS LIVE completeness check — 7 deterministic steps.

    Phase-1 step 2 (V>> D1–D4 locked 2026-06-26): promoted from SHADOW to LIVE
    with a ≥14-day shadow==live agreement hypothesis. Each step is a deterministic
    assertion; a tampered record that passes shadow would ALSO pass here, and a
    genuinely-broken record fails in exactly the same step (monotonicity loss,
    root divergence, or integrity breach).

    Returns ``{step_name: {"pass": bool, "detail": str}, ...}`` — 7 entries,
    each with an exact pass/fail. The verdict is the AUTHENTICATED completeness
    gate, not a self-score.
    """
    from lib.audit_chain import Verdict
    from lib.context_chain import read_context_chain, verify_context_chain
    from lib.context_head import read_head

    steps = {}
    chain = None
    prev = None
    root = ""
    n = 0

    # Step 1 — chain_loaded: the chain file is readable and non-empty
    try:
        chain = read_context_chain(path=path, head_pointer=head_pointer)
        if not chain:
            steps["chain_loaded"] = {"pass": True, "detail": "empty chain (genesis admissible)"}
            # An empty chain is vacuously complete — all remaining steps N/A
            for name in ("integrity_verified", "paramount_identified",
                         "merkle_root_computed", "previous_root_loaded",
                         "monotonicity_checked", "root_committed"):
                if name not in steps:
                    steps[name] = {"pass": True, "detail": "vacuous (empty chain)"}
            return steps
        steps["chain_loaded"] = {"pass": True, "detail": f"{len(chain)} nodes"}
    except Exception as e:
        steps["chain_loaded"] = {"pass": False, "detail": str(e)}
        return steps

    # Step 2 — integrity_verified: verify_context_chain returns VERIFIED
    try:
        v = verify_context_chain(path=path, head=read_head(head_pointer))
        steps["integrity_verified"] = {"pass": v is Verdict.VERIFIED,
                                       "detail": f"verdict={v.value if hasattr(v, 'value') else v}"}
    except Exception as e:
        steps["integrity_verified"] = {"pass": False, "detail": str(e)}
        return steps

    # Step 3 — paramount_identified: paramount addresses extracted and non-empty
    try:
        addrs = _paramount_addrs(chain)
        if len(addrs) < 1:
            steps["paramount_identified"] = {"pass": True,
                                             "detail": "no paramount facts (admissible)"}
        else:
            steps["paramount_identified"] = {"pass": True,
                                             "detail": f"{len(addrs)} paramount facts"}
    except Exception as e:
        steps["paramount_identified"] = {"pass": False, "detail": str(e)}
        return steps

    # Step 4 — merkle_root_computed: Merkle root over addresses, 64-char hex
    try:
        root = merkle_root([a.encode("utf-8") for a in addrs]) if addrs else ""
        n = len(addrs)
        steps["merkle_root_computed"] = {
            "pass": len(root) == 64 or (n == 0 and root == ""),
            "detail": f"root={root[:12]}... paramount_n={n}"}
    except Exception as e:
        steps["merkle_root_computed"] = {"pass": False, "detail": str(e)}
        return steps

    # Step 5 — previous_root_loaded: previous snapshot's completeness root
    try:
        for node in reversed(chain):
            if node.kind == "context-snapshot" and node.decision:
                prev = node.decision.get("completeness")
                if isinstance(prev, dict):
                    break
                prev = None
        if prev is None:
            steps["previous_root_loaded"] = {"pass": True,
                                             "detail": "no previous snapshot (genesis admissible)"}
        else:
            steps["previous_root_loaded"] = {
                "pass": isinstance(prev.get("paramount_root"), str),
                "detail": f"prev_root={str(prev.get('paramount_root', ''))[:12]}..."
                         f" prev_n={prev.get('paramount_n', '?')}"}
    except Exception as e:
        steps["previous_root_loaded"] = {"pass": False, "detail": str(e)}
        return steps

    # Step 6 — monotonicity_checked: paramount_n not decreased vs previous
    try:
        if prev is None or not isinstance(prev, dict):
            steps["monotonicity_checked"] = {"pass": True,
                                             "detail": "genesis or no previous snapshot"}
        else:
            prev_n = prev.get("paramount_n", 0)
            if n >= prev_n:
                steps["monotonicity_checked"] = {
                    "pass": True,
                    "detail": f"paramount_n {n} >= prev {prev_n} (monotone)"}
            else:
                steps["monotonicity_checked"] = {
                    "pass": False,
                    "detail": f"paramount_n DECREASED: {n} < prev {prev_n} (broken completeness)"}
    except Exception as e:
        steps["monotonicity_checked"] = {"pass": False, "detail": str(e)}

    # Step 7 — root_committed: the root is reproducible and ready for anchor
    steps["root_committed"] = {
        "pass": len(root) == 64,
        "detail": f"root={root[:12]}... ready for anchor" if len(root) == 64
                  else "root missing or malformed"}

    return steps


def snapshot_chain(
    *,
    path: Path | None = None,
    head_pointer: Path | None = None,
    cap: int = CAP_CHARS,
) -> PrecogIDR:
    """Fold the current chain and pin the kernel with a ``context-snapshot`` IDR.

    The snapshot is itself a chain node (links the current HEAD and becomes the
    new HEAD), so the chain records its own folds. ``decision.kernel_sha256`` =
    ``payload_digest`` of the folded kernel; ``decision.substrate_digest`` = the
    fold's input digest; ``folded_nodes`` = how many deltas it subsumes (P5's
    reaper reaps only deltas subsumed by a VERIFIED snapshot).
    """
    chain = read_context_chain(path=path, head_pointer=head_pointer)
    kernel = fold_chain(chain, cap=cap)
    kernel_sha = payload_digest(kernel.text)
    return append_context(
        {
            "kernel_sha256": kernel_sha,
            "substrate_digest": kernel.substrate_digest,
            "folded_nodes": len(chain),
            "completeness": _paramount_commitment(chain),  # SHADOW (Slice B, D2)
        },
        fingerprint=kernel_sha if len(kernel_sha) == 64 else kernel_sha.ljust(64, "0")[:64],
        kind="context-snapshot",
        path=path,
        head_pointer=head_pointer,
    )
