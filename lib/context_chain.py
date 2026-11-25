"""Context memory-chain — write / read / verify over signed IDR deltas (P3).

The chain is a linear sequence of ``context-delta`` IDRs (``lib.precog.idr``),
each HMAC-SHA256-signed and linking its predecessor via ``predecessor_idr``. The
genesis delta links to an exogenous anchor (``council:context-chain-genesis``) so
``verify_chain_integrity`` can root the forest. Append is build → append →
atomic HEAD swap; read resolves HEAD and traverses; verify rebuilds the forest
and returns the tri-state ``Verdict``.

Five operations (design §4): write, read, replay (read + re-fold — fold is P4),
audit (this module's ``verify_context_chain``), modify (``context-supersede`` —
a forward-only correction written by ``supersede_context``; view-building readers
drop the target via ``mask_superseded``, history-proving readers do not). This
module is the WRITE+VERIFY side; signing lives in ``lib.precog.idr``, verification in
``lib.idr_forest`` — neither imports HAL/LLM (the P3 import-isolation lint).

Design: GRIP ``drafts/happi-context-event-memory-chain-design.md`` §4-§5.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.audit_chain import Verdict
from lib.context_head import _session_path, read_head, write_head
from lib.idr_forest import (
    IdrForestError,
    ReplayResult,
    _verify_node,
    build_chain_forest,
    replay_chain,
    verify_chain_integrity,
)
from lib.precog.idr import (
    PrecogIDR,
    _canonical_json,
    append_idr,
    build_idr,
    content_addr,
    read_idr_chain,
)

# Public API (#3437, AGORA #3434 defer #3): make the re-export of IdrForestError
# — and the ReplayResult / Verdict types the replay+verify contract returns —
# EXPLICIT, rather than an implicit import-coupling consumers reach via the
# module object. No `from lib.context_chain import *` consumer exists, so this
# documents the surface without changing any current import.
__all__ = [
    "chain_path",
    "diagnose_context_chain",
    "append_context",
    "read_context_chain",
    "verify_context_chain",
    "verdict_of_chain",
    "context_addr",
    "checkpoint",
    "supersede_context",
    "mask_superseded",
    "superseded_ids",
    "replay_context",
    "resolve_citation",
    "blob_path",
    "chain_head_stamp",
    "stamp_view_header",
    "IdrForestError",
    "ReplayResult",
    "Verdict",
]

# The genesis delta's predecessor — an admissible exogenous anchor (council:
# prefix) so the chain roots into a Forest. The chain is signature-verified
# regardless; the anchor gives it an exogenous root per exogenous-anchor-law.
_GENESIS_ANCHOR = "council:context-chain-genesis"

_DEFAULT_CHAIN_PATH = (
    Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
    / "state" / "precog" / "context.jsonl"
)


def chain_path(path: Path | None = None) -> Path:
    # B2 per-session namespacing — the same single shim as before, now resolving
    # the session-scoped sibling of the base (``context.jsonl`` →
    # ``context-a7b2.jsonl``) via the shared ``context_head._session_path`` helper
    # (DRY — chain + HEAD use ONE namespacing rule, so they never disagree on the
    # session segment). Byte-identical legacy path when no session id is set.
    return path or _session_path(_DEFAULT_CHAIN_PATH)


def blob_path(addr: str, *, path: Path | None = None) -> Path:
    """On-disk location of a content-addressed kernel blob (P0 blob-presence axis,
    design §6). The store is a ``blobs/`` sibling of the chain file — so it follows
    ``CLAUDE_DIR`` AND a test's tmp chain path identically — but is NOT
    session-namespaced: a blob is addressed purely by the SHA-256 of its bytes, so
    two sessions that fold to the same kernel share one blob (content-addressed,
    git-tree style). ``path`` (the chain file) only fixes the parent directory; the
    session segment in its filename is irrelevant to ``.parent``."""
    return chain_path(path).parent / "blobs" / addr


def append_context(
    decision: dict[str, Any],
    *,
    fingerprint: str,
    kind: str = "context-delta",
    inputs: dict[str, Any] | None = None,
    model_versions: list[str] | None = None,
    records_idr: str | None = None,
    path: Path | None = None,
    head_pointer: Path | None = None,
) -> PrecogIDR:
    """Append one context delta and atomically swap HEAD to it. Returns the new
    node. The genesis delta (no prior HEAD) links to the exogenous genesis
    anchor so the chain is rooted; later deltas link to the prior context node.

    ``records_idr`` (B1 cross-reference, council wf_e537db61): the ``content_addr``
    of the signed *decision-IDR* (in the separate ``state/precog/idr.jsonl`` ledger)
    that THIS context delta records. It is a CROSS-REFERENCE between two chains, NOT
    the temporal spine — ``predecessor_idr`` remains the same-file root→leaf link and
    is never reused for this. The citation is folded INTO ``decision`` BEFORE signing,
    so a tampered ``records_idr`` byte fails ``verify_context_chain`` (BROKEN). Strictly
    additive: when ``None`` the decision body — and therefore the node's
    ``content_addr`` — is BYTE-IDENTICAL to a pre-B1 record (back-compat). The caller's
    dict is never mutated (a shallow copy carries the citation).
    """
    cpath = chain_path(path)
    prev = read_head(head_pointer)
    if prev is None:
        predecessor, depth = _GENESIS_ANCHOR, 0
    else:
        predecessor = prev
        depth = len(read_idr_chain(predecessor_id=prev, path=cpath))
    inp = dict(inputs or {})
    if model_versions:
        inp.setdefault("model_versions", list(model_versions))
    # Citation folded into the signed body. Copy-on-cite (never mutate the caller's
    # dict); omit the key entirely when uncited so content_addr is byte-unchanged.
    body = decision if records_idr is None else {**decision, "records_idr": records_idr}
    idr = build_idr(
        prompt="",
        fingerprint=fingerprint,
        decision=body,
        predecessor_idr=predecessor,
        depth=depth,
        kind=kind,
        inputs=inp,
    )
    append_idr(idr, path=cpath)
    write_head(idr.id, head_pointer)
    return idr


def read_context_chain(head: str | None = None, path: Path | None = None,
                       head_pointer: Path | None = None) -> list[PrecogIDR]:
    """Resolve HEAD (explicit ``head`` wins; otherwise read the ``head_pointer``
    file) and return the chain root→leaf. Empty list when the chain has no head."""
    cpath = chain_path(path)
    resolved = head if head is not None else read_head(head_pointer)
    if resolved is None:
        return []
    return read_idr_chain(predecessor_id=resolved, path=cpath)


def diagnose_context_chain(path: Path | None = None,
                           head_pointer: Path | None = None) -> dict:
    """WHY the chain verifies as it does. Reports; changes no verdict.

    `verify_context_chain` returns a single tri-state, and on this machine it
    returns BROKEN for a chain in which every signature verifies, every snapshot
    blob is present and hashes back, depths run 0..33 with no duplicates, and
    exactly ONE node names a predecessor absent from the file — consistent with
    `lib.context_gc.reap` having archived a pre-snapshot prefix.

    A reader therefore cannot tell "one ancestor was garbage-collected" from
    "somebody edited a record", which is the only question the chain exists to
    answer. That is the same false-accusation class removed from the packaged
    auditor in GRIP#6207/#6214, where an unlinkable record is excluded and the
    verdict reports its coverage instead of collapsing.

    THIS FUNCTION DELIBERATELY DOES NOT FIX THAT. It changes no verdict and no
    caller's behaviour; it only makes the distinction visible, so the fix
    (GRIP#6220) can be written by someone who can also judge it. Two reasons for
    that restraint, and the second is the load-bearing one:

    - Widening the anchor set to turn BROKEN green would trade a false alarm for
      a false CLEARANCE, which is strictly worse on a tamper-evidence leg.
    - GRIP#6203 made a commitment's belief binding depend on this chain
      verifying, so a change of mine that makes it green would make my own merged
      work look sound. That is close enough to self-ratification to leave alone
      (`exogenous-anchor-law.md`).

    Returns `signatures_failing`, `blobs_missing_or_altered`, `dangling`
    (node/kind/absent-predecessor triples), `nodes`, and `depth_duplicates`. A
    chain whose ONLY finding is `dangling` is reaped, not tampered — and the
    verdict will still say BROKEN, which is why this exists.
    """
    # `head_pointer` is NOT optional decoration. Without it this function took
    # `path` and resolved HEAD from the DEFAULT pointer, so on any chain but the
    # live one it read ZERO nodes and reported a clean bill — an unexpressible
    # parameter silently using the default, which is the class documented in
    # `reference-an-unexpressible-parameter-silently-uses-the-default` hours
    # before this function was written, committed inside the function written to
    # investigate a defect of that class. Caught by its own first test run.
    chain = read_context_chain(path=path, head_pointer=head_pointer)
    ids = {n.id for n in chain}
    depths = [getattr(n, "depth", -1) for n in chain]
    # An ADMISSIBLE EXOGENOUS ANCHOR is not dangling. The genesis delta links to
    # `council:context-chain-genesis` by design, and a prefixed anchor is a root
    # rather than a missing node -- the same distinction `idr_chain.__main__`
    # already draws with its own ADMISSIBLE tuple. Omitting it here reported a
    # perfectly healthy chain as having a dangling ancestor, which the control
    # test caught on the first run.
    admissible = ("ci:", "human:", "council:", "hypo:")
    dangling = [
        {"node": n.id, "kind": getattr(n, "kind", "?"),
         "absent_predecessor": n.predecessor_idr}
        for n in chain
        if (pred := getattr(n, "predecessor_idr", None))
        and pred not in ids
        and not pred.startswith(admissible)
    ]
    failing = [n.id for n in chain
               # Compare the ENUM MEMBER, never its repr. `Verdict` is a
               # `(str, Enum)`, so `str()` gives "Verdict.VERIFIED" on every
               # Python to date -- but 3.11 changed `__str__` for enum
               # subclasses, and converting this to the modern `enum.StrEnum`
               # would make it "verified", at which point a repr comparison
               # reports EVERY node as failing and a genuine reap reads as
               # tampering. Identity is immune to both.
               if _verify_node(n) is not Verdict.VERIFIED]
    snaps = [n for n in chain if getattr(n, "kind", "") == "context-snapshot"]
    bad_blobs = [n.id for n in snaps if not _snapshot_blob_intact(n, path)]
    return {
        "nodes": len(chain),
        "signatures_failing": failing,
        "blobs_missing_or_altered": bad_blobs,
        "dangling": dangling,
        "depth_duplicates": len(depths) - len(set(depths)),
        "reaped_not_tampered": bool(dangling) and not failing and not bad_blobs,
    }


def verify_context_chain(path: Path | None = None, head: str | None = None) -> Verdict:
    """Rebuild the forest from the chain and return the integrity ``Verdict``.

    TWO axes (design §6), both required for ``VERIFIED``:
      (a) **spine HMAC** — every node's HMAC matches its body; a flipped delta byte
          drives it to ``BROKEN`` (``verify_chain_integrity``, pure-HMAC, no LLM).
      (b) **blob-presence** — a ``context-snapshot`` that carries a kernel blob
          reference (``decision.kernel_blob_addr``) is ``BROKEN`` unless that blob
          EXISTS on disk AND its bytes hash back to the recorded address. This
          closes the dangling-pointer class: a reaped/deleted snapshot blob would
          otherwise read ``VERIFIED`` (the address is signed, not the bytes) while
          ``/recall`` rehydrates nothing — VERIFIED-but-gone. A SHA-256 is
          tamper-*evidence*, not tamper-*recovery*; (b) adds the existence proof.

    Axis (b) is **monotone-toward-safe and inert by construction**: it can only
    turn a spine-VERIFIED-but-dangling result ``BROKEN``, never a sound node
    broken, and a snapshot WITHOUT a ``kernel_blob_addr`` (every snapshot written
    before this axis ships) is not blob-backed → not checked → its verdict is
    unchanged. So no existing chain regresses. An empty chain is vacuously
    ``VERIFIED``.

    Reaped-chain boundary (P5): after ``lib.context_gc.reap`` archives the
    pre-snapshot prefix, the live root is a ``context-snapshot`` whose predecessor
    was moved to the cold archive. A verified fold IS an exogenous checkpoint, so
    that archived predecessor is admitted as a boundary root — but ONLY when the
    root node is a ``context-snapshot``; a non-snapshot dangling predecessor stays
    a real ``BROKEN`` (genuine missing-link corruption). Forest edges are
    structural only — ``verify_chain_integrity`` checks each node's own HMAC, so
    admitting the boundary never weakens tamper-detection.
    """
    return verdict_of_chain(read_context_chain(head=head, path=path), path)


def verdict_of_chain(chain: list[PrecogIDR], path: Path | None = None) -> Verdict:
    """The verdict of a chain that has already been read, root to leaf, by the same
    steps ``verify_context_chain`` applies after its read (both axes above, and the
    P5 boundary through ``_forest_from_chain``). ``path`` is the chain file, used
    only to find snapshot blobs. Split out so the warm-start resolver can judge
    each candidate on a prefix of ONE read instead of re-reading the file per
    candidate (GRIP#6220). ``chain`` must be a reader walk from
    ``read_context_chain``, or a prefix of one, unmasked and unedited: the verdict
    is about exactly the records passed, so a masked or hand-built list gets a
    verdict on that list, never on the file."""
    if not chain:
        return Verdict.VERIFIED
    try:
        forest = _forest_from_chain(chain)
    except IdrForestError:
        return Verdict.BROKEN  # a dangling mid-chain link is genuine corruption
    verdict = verify_chain_integrity(forest)  # axis (a) — spine HMAC (unchanged)
    if not _snapshot_blobs_present(chain, path):  # axis (b) — blob-presence
        return Verdict.BROKEN  # a dangling/corrupt referenced blob dominates
    return verdict


def _snapshot_blobs_present(chain: list[PrecogIDR], path: Path | None) -> bool:
    """True iff every BLOB-BACKED ``context-snapshot`` in the chain has its
    referenced kernel blob present-and-intact on disk. A snapshot is blob-backed
    iff its ``decision`` carries a non-empty ``kernel_blob_addr``; snapshots
    without it (all pre-P0 snapshots) are skipped → inert. Pure read; never
    raises (a probe never breaks the verify path — a read error counts as a
    missing blob, i.e. ``False``, which is the safe direction)."""
    return all(
        _snapshot_blob_intact(node, path)
        for node in chain
        if node.kind == "context-snapshot"
    )


def _snapshot_blob_intact(node: PrecogIDR, path: Path | None) -> bool:
    """Per-snapshot axis-(b) check. ``True`` when the snapshot is NOT blob-backed
    (no ``kernel_blob_addr`` — the inert default), OR its blob file exists and its
    bytes hash (SHA-256) back to the recorded address. ``False`` (→ ``BROKEN``)
    when a recorded blob is missing or its bytes do not match the address
    (corruption). The recorded address is what the spine HMAC signs, so a tampered
    address is already caught by axis (a); axis (b) covers EXISTENCE of the bytes
    that address points at."""
    addr = (node.decision or {}).get("kernel_blob_addr")
    if not addr:
        return True  # not blob-backed → inert, never checked
    try:
        blob = blob_path(str(addr), path=path)
        if not blob.is_file():
            return False  # recorded blob is gone — the dangling-pointer class
        return hashlib.sha256(blob.read_bytes()).hexdigest() == addr
    except OSError:
        return False  # unreadable counts as missing — the safe direction


def resolve_citation(delta: PrecogIDR, idr_path: Path | None = None) -> str:
    """Read-side resolver for a context delta's cross-reference citation (B1).

    Returns ``"RESOLVED"`` when the delta's ``decision[records_idr]`` names a
    ``content_addr`` present in the decision-IDR ledger (``state/precog/idr.jsonl``),
    ``"DANGLING"`` when the cited address is absent. An UNCITED delta (no
    ``records_idr``) is vacuously ``"RESOLVED"`` — there is no reference to dangle.

    Fail-open by contract — NEVER raises: any ledger read error / malformed node /
    missing field degrades to ``"RESOLVED"`` (the citation is treated as not-yet-
    refutable, never as a hard failure). This is a pure, signature-INDEPENDENT
    cross-reference probe: a ``DANGLING`` citation does NOT make the chain ``BROKEN``
    (the signed link is ``predecessor_idr``; the citation is a separate axis), so
    ``verify_context_chain`` stays ``VERIFIED`` even when ``resolve_citation`` is
    ``DANGLING``. Imports no HAL/LLM (preserves the P3 import-isolation lint).
    """
    try:
        cited = (delta.decision or {}).get("records_idr")
        if not cited:
            return "RESOLVED"  # uncited — nothing to dangle
        ledger = read_idr_chain(predecessor_id=None, path=idr_path)
        present = {content_addr(asdict(node)) for node in ledger}
        return "RESOLVED" if cited in present else "DANGLING"
    except Exception:  # fail-open: a probe never breaks the read path
        return "RESOLVED"


def context_addr(idr: PrecogIDR) -> str:
    """Content address of a context node — the dedup/merkle coordinate. Thin
    re-export of ``lib.precog.idr.content_addr`` over the node's envelope
    (excludes the volatile id/ts/audit fields)."""
    return content_addr(asdict(idr))


def checkpoint(
    next_step: dict | None,
    summary: str = "",
    *,
    title: str | None = None,
    tier: str = "feedback",
    paramount: bool = False,
    records_idr: str | None = None,
    path: Path | None = None,
    head_pointer: Path | None = None,
    model_versions: list[str] | None = None,
) -> PrecogIDR:
    """Write ONE signed ``context-delta`` carrying the volatile ``next_step`` BY
    VALUE + a kernel ``summary``, threading the predecessor — the read-from-previous
    chain: each delta links the one before, which linked ITS predecessor. The
    genesis call (no prior HEAD) roots at the exogenous anchor via ``append_context``,
    so the FIRST checkpoint seeds the chain.

    HARNESS-NEUTRAL (design D1, V>> 2026-06-07): imports no HAL/LLM and takes no
    Claude-Code object — any HAPPI harness invokes this on its own checkpoint
    cadence (a ``/save``, an end-of-request, an explicit user checkpoint). The
    "turn" notion lives only in the per-harness caller. ``next_step`` is stored by
    VALUE (the ~1KB volatile mental-model summary P6's warm-start reads); the bulk
    ~40KB kernel is carried BY REFERENCE on the periodic snapshot (``kernel_sha256``),
    never embedded here (design §4 read-by-reference).
    """
    decision = {
        "next_step": next_step if isinstance(next_step, dict) else {},
        "summary": str(summary or "")[:600],
        "title": title or "context checkpoint",
        "tier": tier,
        "paramount": bool(paramount),
    }
    fingerprint = hashlib.sha256(_canonical_json(decision).encode("utf-8")).hexdigest()
    return append_context(
        decision,
        fingerprint=fingerprint,
        kind="context-delta",
        records_idr=records_idr,
        model_versions=model_versions,
        path=path,
        head_pointer=head_pointer,
    )


# ---------------------------------------------------------------------------
# context-supersede — the forward-only correction (MEMORY_CONTEXT.md)
# ---------------------------------------------------------------------------

_SUPERSEDE_KIND = "context-supersede"
_SUPERSEDE_RESERVED = ("supersedes", "supersedes_addr", "reason")


def superseded_ids(chain: list[PrecogIDR]) -> set[str]:
    """Ids named as ``decision.supersedes`` by any ``context-supersede`` node in
    ``chain``. Pure; a malformed supersede (no string target) names nothing."""
    out: set[str] = set()
    for node in chain:
        if node.kind != _SUPERSEDE_KIND:
            continue
        target = (node.decision or {}).get("supersedes")
        if isinstance(target, str) and target:
            out.add(target)
    return out


def mask_superseded(chain: list[PrecogIDR]) -> list[PrecogIDR]:
    """The chain as a VIEW-building reader sees it: every node that a later
    ``context-supersede`` names is dropped; order and every other node are
    untouched. Pure — the file, the ids and the signatures never change, which
    is why ``verify_context_chain`` reads the UNMASKED chain and stays
    ``VERIFIED``. A supersede that is itself superseded is masked too, and its
    own target stays masked: corrections correct forward, never back."""
    hidden = superseded_ids(chain)
    return [node for node in chain if node.id not in hidden]


def supersede_context(
    target_id: str,
    correction: dict[str, Any],
    *,
    reason: str,
    records_idr: str | None = None,
    model_versions: list[str] | None = None,
    path: Path | None = None,
    head_pointer: Path | None = None,
) -> PrecogIDR:
    """Append a ``context-supersede`` node — MEMORY_CONTEXT.md §context-supersede:
    "a correction is a new node that the fold masks the old one with — history
    is never rewritten, only superseded forward."

    The node's signed ``decision`` is the ``correction`` (any fields a delta may
    carry: title / summary / next_step / tier / paramount …) plus three link
    fields the caller may not set: ``supersedes`` (the target's id),
    ``supersedes_addr`` (the target's content address, so the correction binds
    the exact bytes it replaces, not just a name) and ``reason``. Readers that
    build a VIEW (the fold, warm-start) drop the target via ``mask_superseded``;
    readers that prove HISTORY (``verify_context_chain``, the paramount
    completeness commitment in ``lib.context_snapshot``) do not.

    Refuses with ``ValueError`` BEFORE any I/O when ``reason`` is blank, the
    correction carries a reserved key, the target is not in the live chain (a
    dangling correction), or the target is a ``context-snapshot`` (a fold pin
    is not a memory claim — correcting a fold is a re-fold).
    """
    if not str(reason or "").strip():
        raise ValueError("supersede_context: a non-empty reason is required")
    forged = [key for key in _SUPERSEDE_RESERVED if key in correction]
    if forged:
        raise ValueError(
            f"supersede_context: the correction may not set reserved key(s) {forged}"
        )
    chain = read_context_chain(path=path, head_pointer=head_pointer)
    target = next((node for node in chain if node.id == target_id), None)
    if target is None:
        raise ValueError(f"supersede_context: target {target_id!r} is not in the chain")
    if target.kind == "context-snapshot":
        raise ValueError(
            "supersede_context: a context-snapshot cannot be superseded — re-fold instead"
        )
    decision = {
        **correction,
        "supersedes": target.id,
        "supersedes_addr": context_addr(target),
        "reason": str(reason).strip(),
    }
    fingerprint = hashlib.sha256(_canonical_json(decision).encode("utf-8")).hexdigest()
    return append_context(
        decision,
        fingerprint=fingerprint,
        kind=_SUPERSEDE_KIND,
        records_idr=records_idr,
        model_versions=model_versions,
        path=path,
        head_pointer=head_pointer,
    )


def _forest_from_chain(chain: list[PrecogIDR]):
    """Build the verified forest from a context chain — shared by verify + replay
    (DRY). Admits a reaped ``context-snapshot`` as the lone sanctioned non-genesis
    boundary root (P5): a reaped chain's root is a snapshot whose archived
    predecessor is gone from the file. ``build_chain_forest`` encapsulates that lone
    admission (no raw ``Forest(...)`` at the call site); a non-snapshot dangling
    predecessor is NOT named, so ``add_idr`` raises ``IdrForestError`` → the caller
    maps it to ``BROKEN`` (genuine mid-chain corruption)."""
    first = chain[0]
    present = {n.id for n in chain}
    snapshot_boundary = (
        first.predecessor_idr
        if (first.predecessor_idr not in (None, _GENESIS_ANCHOR)
            and first.predecessor_idr not in present
            and first.kind == "context-snapshot")
        else None
    )
    return build_chain_forest(
        chain, genesis_anchor=_GENESIS_ANCHOR, snapshot_boundary=snapshot_boundary
    )


def replay_context(head: str | None = None, path: Path | None = None) -> ReplayResult | None:
    """Deterministically REPLAY GRIP's own context memory chain root→HEAD — the
    audit / replay / analyse primitive over the signed memory substrate. Pure +
    PROVIDER-AGNOSTIC (no LLM): replay is re-derivation from the signed chain, which
    is why it is byte-stable and reproducible (``ReplayResult.replay_digest``).

    Returns ``None`` when the chain is empty/unresolvable OR structurally
    unbuildable — a dangling non-snapshot link makes ``_forest_from_chain`` raise
    ``IdrForestError``; we map that to ``None`` to honour the documented contract
    (AGORA #3434), rather than leak an unhandled exception. A chain that BUILDS but
    fails its HMAC surfaces ``BROKEN`` in the verdict via ``replay_chain``. Proves
    the core IDR-chain purpose: the memory is auditable (verdict), replayable
    (digest stable across runs) and analysable (the reconstructed sequence)."""
    resolved = head if head is not None else read_head()
    if resolved is None:
        return None
    chain = read_context_chain(head=resolved, path=path)
    if not chain:
        return None
    try:
        forest = _forest_from_chain(chain)
    except IdrForestError:
        return None  # structurally-unbuildable chain → nothing to replay (contract)
    return replay_chain(forest, resolved)


# ---------------------------------------------------------------------------
# Freshness stamp (P0 write-side only — design §1 commitment 3, §5 P0)
# ---------------------------------------------------------------------------

# Matches a single stamp comment line so ``stamp_view_header`` is idempotent:
# re-stamping a view replaces the prior stamp rather than accreting copies.
_STAMP_RE = re.compile(r"^<!-- chain-HEAD:.*?-->\n", re.MULTILINE)


def chain_head_stamp(node_id: str, ts: str | None = None) -> str:
    """The freshness-stamp comment line a derived view carries so a later phase
    (P3+) can tell whether the view is current (its stamped HEAD == ``read_head``)
    or stale → re-fold. ``ts`` defaults to an ISO-8601 UTC instant. WRITE-SIDE
    ONLY in P0 — nothing parses it yet (design §5 P0: "nothing reads the stamp")."""
    when = ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"<!-- chain-HEAD: {node_id} · folded-at: {when} -->"


def stamp_view_header(text: str, node_id: str, ts: str | None = None) -> str:
    """Return ``text`` with the freshness stamp as its FIRST line — idempotently
    (any existing ``chain-HEAD`` stamp lines are stripped first, so repeated
    stamping never accretes). Placing the stamp ABOVE any
    ``<!-- substrate-digest: … -->`` marker keeps it inert to
    ``holistic_synthesiser.payload_digest`` (which reads from that marker onward),
    so the R2/R3 idempotency anchor and the write-skip no-op guard are unchanged —
    the stamp adds a header comment, never a body or digest change. Pure."""
    stripped = _STAMP_RE.sub("", text)
    return f"{chain_head_stamp(node_id, ts)}\n{stripped}"
