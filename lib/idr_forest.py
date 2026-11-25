"""IDR Forest — schema + canonicalising projection (P0 + P1 skeleton).

Origin: 2026-05-24, GRIP #2799 / Task #1872 Wave 3-A of
``plans/tender-popping-catmull.md``. Spec:
``docs/adr/0009-idr-forest-spec.md``. Master plan:
``drafts/ultraplan-idr-replay-forest-banach-2026-05-17.md``.

This module ships the **Forest data structure** that organises PrecogIDR
envelopes into a provenance graph rooted at exogenous anchors only, plus
the canonicalising projection ℱ (``canonicalise``) and the tamper-detection
verifier (``verify_chain_integrity``).

P1 ships:
* ``ForestNode`` + ``Forest`` frozen dataclasses (immutable forests).
* ``EXOGENOUS_ANCHOR_PATTERNS`` — admissible anchor-id prefixes per
  ``rules/exogenous-anchor-law.md``.
* ``add_idr`` — pure builder; never mutates the input forest.
* ``canonicalise`` — the Banach projector ℱ (re-serialises every node to
  canonical JSON, re-links edges from ``predecessor_idr``); idempotent.
* ``verify_chain_integrity`` — composes ``lib.audit_chain.verify_entry``
  (Ed25519 Round 6) and surfaces a tri-state verdict over the whole
  forest.
* ``find_unanchored`` — pure structural check; lists every node whose
  chain does NOT trace to an exogenous root.

C5: this module composes ``lib.precog.idr``, ``lib.audit_chain``, and
``lib.banach_projector``. It does NOT re-implement signing, chain hashing,
or fixed-point iteration.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict, replace
from typing import Iterator, Mapping

from lib.audit_chain import Verdict, compute_entry_hash
from lib.merkle import inclusion_proof, merkle_root, verify_inclusion
from lib.precog.idr import PrecogIDR, _canonical_json, _sign_placeholder, content_addr

# ---------------------------------------------------------------------------
# Exogenous anchor admissibility (C3 — exogenous-anchor law)
# ---------------------------------------------------------------------------

# Root id prefixes admissible as exogenous anchors. The four admissible
# anchor classes from ``rules/exogenous-anchor-law.md``:
# 1. CI on mutation-surviving tests
# 2. Human-authored commit/merge SHA
# 3. Cross-provider verdict
# 4. Pre-registered hypothesis id
#
# A future fifth anchor (V>> R&D) extends this tuple — do NOT add a class
# without first registering it in the canonical rule.
EXOGENOUS_ANCHOR_PATTERNS: tuple[str, ...] = (
    "ci:",
    "human:",
    "council:",
    "hypo:",
)


class IdrForestError(ValueError):
    """Raised when a forest contract is violated.

    Specifically:
    * An IDR claims a predecessor that does not exist in the forest.
    * A root id does not match any ``EXOGENOUS_ANCHOR_PATTERNS`` prefix.
    * A node id collides with an existing node id.

    Callers branch on this exception class — do NOT raise a sibling type.
    """


# ---------------------------------------------------------------------------
# Forest schema (immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForestNode:
    """One node of the IDR forest.

    ``idr`` is the happi/1.1 envelope from ``lib.precog.idr``. ``anchor_id``
    is the nearest exogenous-root ancestor's id — ``None`` if the node is
    unanchored (and therefore not trusted).
    """

    idr: PrecogIDR
    anchor_id: str | None


@dataclass(frozen=True)
class Forest:
    """Immutable forest of IDR nodes rooted at exogenous anchors.

    * ``roots`` is the tuple of admissible exogenous anchor ids.
    * ``nodes`` maps every node id to its ``ForestNode``.
    * ``edges`` maps every node id to its parent's id (or ``None`` at a
      root edge — the parent IS one of the ``roots``).

    Constructed via ``empty_forest()`` + ``add_idr()`` — never directly.
    """

    roots: tuple[str, ...]
    nodes: Mapping[str, ForestNode]
    edges: Mapping[str, str | None]


def _is_admissible_anchor(anchor_id: str) -> bool:
    """``True`` if ``anchor_id`` matches an admissible exogenous-anchor prefix."""
    return any(anchor_id.startswith(prefix) for prefix in EXOGENOUS_ANCHOR_PATTERNS)


def empty_forest(roots: tuple[str, ...]) -> Forest:
    """Build a forest rooted at the given exogenous anchors.

    Raises ``IdrForestError`` if any root id does not match an admissible
    anchor pattern (``ci:`` / ``human:`` / ``council:`` / ``hypo:``).
    """
    if not roots:
        raise IdrForestError("empty_forest requires at least one root anchor")
    bad = tuple(r for r in roots if not _is_admissible_anchor(r))
    if bad:
        raise IdrForestError(
            f"non-exogenous root ids rejected: {bad!r}. "
            f"Admissible prefixes: {EXOGENOUS_ANCHOR_PATTERNS}"
        )
    return Forest(roots=tuple(roots), nodes={}, edges={})


# ---------------------------------------------------------------------------
# Forest builders (pure — never mutate input)
# ---------------------------------------------------------------------------


def add_idr(
    forest: Forest,
    idr: PrecogIDR,
    *,
    anchor_id: str | None = None,
) -> Forest:
    """Add one IDR node to ``forest``. Returns a new forest (immutable input).

    The ``anchor_id`` argument names the nearest exogenous root ancestor.
    When omitted, the resolver walks the predecessor chain looking for a
    root match; if none is found, the node lands as ``unanchored``
    (``anchor_id=None``).

    Raises ``IdrForestError`` if:
    * The node id already exists in the forest.
    * The IDR's ``predecessor_idr`` is set but no matching node exists.
    """
    if idr.id in forest.nodes:
        raise IdrForestError(
            f"node id collision: {idr.id!r} already exists in forest"
        )
    if idr.predecessor_idr is not None and idr.predecessor_idr not in forest.nodes:
        # Predecessor must be a root anchor OR a known node.
        if idr.predecessor_idr not in forest.roots:
            raise IdrForestError(
                f"node {idr.id!r}: predecessor {idr.predecessor_idr!r} "
                f"is neither a root anchor nor a known node"
            )

    resolved_anchor = anchor_id
    if resolved_anchor is None:
        resolved_anchor = _resolve_anchor(forest, idr)

    new_node = ForestNode(idr=idr, anchor_id=resolved_anchor)
    new_nodes = dict(forest.nodes)
    new_nodes[idr.id] = new_node
    new_edges = dict(forest.edges)
    new_edges[idr.id] = idr.predecessor_idr
    return replace(forest, nodes=new_nodes, edges=new_edges)


def _resolve_anchor(forest: Forest, idr: PrecogIDR) -> str | None:
    """Walk the predecessor chain to the nearest exogenous root, if any."""
    current_pred = idr.predecessor_idr
    visited: set[str] = set()
    while current_pred is not None and current_pred not in visited:
        visited.add(current_pred)
        if current_pred in forest.roots:
            return current_pred
        parent_node = forest.nodes.get(current_pred)
        if parent_node is None:
            return None
        if parent_node.anchor_id is not None:
            return parent_node.anchor_id
        current_pred = parent_node.idr.predecessor_idr
    return None


def build_chain_forest(
    chain: list[PrecogIDR],
    *,
    genesis_anchor: str,
    snapshot_boundary: str | None = None,
) -> Forest:
    """Build a forest from a linear context chain (root→leaf) via the sanctioned
    ``add_idr`` builder — the IN-MODULE home for the one construction the ``Forest``
    docstring forbids at call sites ("Constructed via empty_forest() + add_idr() —
    never directly"). Callers (e.g. ``lib.context_chain.verify_context_chain``) use
    THIS, never a raw ``Forest(...)``.

    Roots = ``(genesis_anchor,)`` plus, when the chain was reaped (its root is a
    ``context-snapshot`` whose archived predecessor is named via
    ``snapshot_boundary``), that boundary id. A verified context-snapshot is an
    exogenous-equivalent checkpoint, so its archived predecessor is the LONE
    sanctioned non-prefix root. The non-reaped path delegates to ``empty_forest``
    (full exogenous-prefix gate); the reaped path admits exactly that one boundary,
    with ``genesis_anchor`` itself still gated.

    Every node is added via ``add_idr``, so linkage IS enforced: a genuine dangling
    mid-chain predecessor raises ``IdrForestError`` (the caller maps that to
    ``BROKEN``). ``verify_chain_integrity`` then checks each node's own HMAC, so the
    boundary admission never weakens tamper-detection. Pure — never mutates input.
    """
    if snapshot_boundary is None:
        forest = empty_forest((genesis_anchor,))  # standard, fully exogenous-gated
    else:
        if not _is_admissible_anchor(genesis_anchor):
            raise IdrForestError(
                f"genesis anchor {genesis_anchor!r} is not an admissible exogenous anchor"
            )
        forest = Forest(roots=(genesis_anchor, snapshot_boundary), nodes={}, edges={})
    for node in chain:
        forest = add_idr(forest, node)
    return forest


# ---------------------------------------------------------------------------
# ℱ — the Banach projector (canonicalising re-projection)
# ---------------------------------------------------------------------------


def canonicalise(forest: Forest) -> Forest:
    """The ℱ projection — return a canonicalised copy of ``forest``.

    Re-serialises every IDR via ``_canonical_json(asdict(idr))`` so any
    byte-level drift surfaces; re-links edges from ``idr.predecessor_idr``
    so a stale ``edges`` map cannot mask a tampered pointer.

    Idempotent: ``canonicalise(canonicalise(F)) == canonicalise(F)`` (the
    second call observes byte-identical input and returns a structurally
    equal forest). T-I3 anchors this property.

    Pure — never mutates ``forest``.
    """
    new_nodes: dict[str, ForestNode] = {}
    new_edges: dict[str, str | None] = {}
    for node_id, node in forest.nodes.items():
        canonical_idr = _round_trip_idr(node.idr)
        new_nodes[node_id] = ForestNode(
            idr=canonical_idr,
            anchor_id=node.anchor_id,
        )
        new_edges[node_id] = canonical_idr.predecessor_idr
    return Forest(roots=forest.roots, nodes=new_nodes, edges=new_edges)


def _round_trip_idr(idr: PrecogIDR) -> PrecogIDR:
    """Serialise an IDR to canonical JSON and parse it back.

    A pure data-driven round-trip — surfaces byte-level drift via the
    deterministic ``_canonical_json`` encoder.
    """
    canonical_blob = _canonical_json(asdict(idr))
    parsed: dict = json.loads(canonical_blob)
    # ``if k in parsed`` keeps the round-trip robust to optional fields absent on
    # legacy nodes (e.g. ``decision_anatomy``) — they fall back to dataclass
    # defaults rather than raising KeyError mid-canonicalisation.
    return PrecogIDR(**{k: parsed[k] for k in PrecogIDR.__dataclass_fields__ if k in parsed})


# ---------------------------------------------------------------------------
# Verification (tri-state per audit_chain.Verdict)
# ---------------------------------------------------------------------------


def signed_body(idr: PrecogIDR) -> dict:
    """The exact envelope body that was SIGNED — the one canonical derivation.

    Every sign and verify path MUST derive the body identically or a legitimate
    record false-fails ``BROKEN``. This is that single derivation, shared by the
    in-house verifier (``_verify_node``) and the external-auditor verifier
    (``lib.idr_external_read.verify_idr_external``) so the two can never drift.

    Two rules, both mirroring ``lib.precog.idr._sign_real``:

    * ``audit`` is excluded — the signature cannot cover itself.
    * an ABSENT ``decision_anatomy`` is dropped rather than sent as ``null``. A
      record with no anatomy was signed over a body without the key at all (and
      every legacy record never had it); dataclass reconstruction defaults it to
      ``None``, so leaving it in would hash a field that was never signed and
      flip VERIFIED -> BROKEN. A PRESENT anatomy WAS signed in and stays in the
      body, tamper-checked with everything else.
    """
    body = {k: v for k, v in asdict(idr).items() if k != "audit"}
    if body.get("decision_anatomy") is None:
        body.pop("decision_anatomy", None)
    return body


def signed_entry_hash(idr: PrecogIDR) -> str:
    """The signed body digest — the message both limbs of a dual signature cover.

    ``compute_entry_hash(signed_body(idr))``. This is the message an external
    auditor must recompute to check a dual signature without trusting GRIP.
    """
    return compute_entry_hash(signed_body(idr))


def verify_chain_integrity(forest: Forest) -> Verdict:
    """Verify every node's audit signature against its envelope body.

    PrecogIDR signs with ``hmac-sha256`` (``lib.precog.idr._sign_real``) —
    measured 2026-08-17: 607 of 607 live leaves carry that scheme, zero
    carry ``sha256-placeholder``, which is retained only for READING
    legacy leaves (``_sign_placeholder``) and is never emitted. This
    verifier dispatches by the ``audit.scheme`` field, so a scheme
    migration (e.g. the Ed25519 + ML-DSA-65 custody path) stays a
    one-line case branch.

    Returns:
        ``Verdict.VERIFIED`` iff every node verifies.
        ``Verdict.BROKEN`` if any node's signature does not match its
        recomputed body digest (tamper detected).
        ``Verdict.DEGRADED`` if a node uses a recognised scheme whose
        verifying key material is currently unavailable (Ed25519 without
        keys — Wave C concern).

    T-I1 anchors this — flipping any byte of a node's IDR body without
    re-signing drives the verdict to ``BROKEN``.
    """
    overall = Verdict.VERIFIED
    for node in forest.nodes.values():
        verdict = _verify_node(node.idr)
        if verdict is Verdict.BROKEN:
            return Verdict.BROKEN
        if verdict is Verdict.DEGRADED:
            overall = Verdict.DEGRADED
    return overall


def _verify_node(idr: PrecogIDR) -> Verdict:
    """Verify one PrecogIDR audit signature by scheme.

    sha256-placeholder → DEGRADED (structural only, not tamper-evident).
    hmac-sha256 → VERIFIED if MAC matches, BROKEN if tampered.
    Unknown / ed25519 (provisioned separately) → DEGRADED.
    """
    scheme = idr.audit.get("scheme") if isinstance(idr.audit, dict) else None
    body = signed_body(idr)  # the ONE signed-body derivation (see signed_body)
    if scheme == "sha256-placeholder":
        # _sign_placeholder imported at module scope above.
        if _sign_placeholder(body).get("signature") != idr.audit.get("signature"):
            return Verdict.BROKEN
        return Verdict.DEGRADED
    if scheme == "hmac-sha256":
        import hashlib as _h, hmac as _hmac
        from lib.audit_chain import _ensure_signing_key
        entry_hash = compute_entry_hash(body)
        key = _ensure_signing_key()
        # stored signature is always "hmac-sha256:<hex>" — _sign_real guarantees the prefix.
        expected = "hmac-sha256:" + _hmac.new(key, entry_hash.encode(), _h.sha256).hexdigest()
        return Verdict.VERIFIED if idr.audit.get("signature") == expected else Verdict.BROKEN
    if scheme == "ed25519+ml-dsa-65":
        # Dual asymmetric + post-quantum custody (D2). Recompute the SAME body
        # digest the signer signed, then fail-closed dual-verify against the
        # keys pinned by per-limb fingerprint and located via the audit's
        # tenant_id. Deterministic — no LLM on the verify path.
        # Wrapped fail-safe (AGORA #3731): an unexpected error here — e.g. a
        # broken import of the verify deps — must NOT crash the whole chain
        # verifier. It degrades to "could not verify" (DEGRADED), consistent
        # with the keys-unavailable path, never an unhandled exception.
        try:
            from lib.pq_dual_sign import dual_verify
            entry_hash = compute_entry_hash(body)
            return dual_verify(entry_hash.encode(), idr.audit, idr.audit.get("tenant_id"))
        except Exception:  # noqa: BLE001 — verifier must never raise; degrade instead
            return Verdict.DEGRADED
    # ed25519 (single): wired via build_idr(tenant_id=...) when per-tenant keys are provisioned.
    return Verdict.DEGRADED


def find_unanchored(forest: Forest) -> tuple[str, ...]:
    """Return the ids of every node whose chain does NOT reach an exogenous root.

    Pure structural check. A node is unanchored iff
    ``node.anchor_id is None``. T-I4-adjacent — this scaffolds the
    unanchored-detection property the ultraplan §1 calls for.
    """
    return tuple(
        node_id for node_id, node in forest.nodes.items() if node.anchor_id is None
    )


# ---------------------------------------------------------------------------
# Iteration helpers (read-only)
# ---------------------------------------------------------------------------


def iter_nodes_topo(forest: Forest) -> Iterator[ForestNode]:
    """Yield nodes in depth order (roots first), stable across runs.

    Single-pass, O(N log N) by ``depth`` then ``id``. Pure read.
    """
    ordered = sorted(
        forest.nodes.values(),
        key=lambda n: (n.idr.depth, n.idr.id),
    )
    yield from ordered


# ---------------------------------------------------------------------------
# P2/P3 — replay primitives (wired in P1), edit-distance metric (wired in P3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayResult:
    """Deterministic replay of a signed chain root→node — the audit / replay /
    analyse primitive.

    PURE + PROVIDER-AGNOSTIC. Replay is re-derivation from the signed, recorded
    chain, NOT LLM re-execution: stochastic LLM output can never be byte-identical
    across providers or runs, and re-execution would put an LLM on the audit path
    (the import-isolation lint forbids it). The decision the chain records IS the
    data — replaying it is the deterministic fold, full stop. This is precisely WHY
    replay is byte-stable and provider-agnostic.

    * ``sequence`` — the reconstructed root→node audit trail (ANALYSABLE).
    * ``verdict`` — integrity over the replayed prefix (AUDITABLE).
    * ``replay_digest`` — sha256 over the canonical sequence; byte-identical across
      independent runs of the same forest (REPLAYABLE). A differing digest on
      re-run means the chain itself changed — the exogenous determinism anchor,
      not a second LLM provider.
    """

    node_id: str
    verdict: Verdict
    sequence: tuple[PrecogIDR, ...]
    replay_digest: str


def _ancestry_root_to_node(forest: Forest, node_id: str) -> tuple[PrecogIDR, ...]:
    """Deterministic root→node ancestry via the forest edge map. Cycle-safe; stops
    at the root edge (``edges[id] is None``) or a boundary the forest does not hold
    (e.g. a reaped snapshot predecessor)."""
    seq: list[PrecogIDR] = []
    cur: str | None = node_id
    seen: set[str] = set()
    while cur is not None and cur in forest.nodes and cur not in seen:
        seen.add(cur)
        seq.append(forest.nodes[cur].idr)
        cur = forest.edges.get(cur)
    seq.reverse()
    return tuple(seq)


def replay_deterministic(forest: Forest, node_id: str) -> PrecogIDR:
    """Deterministically REPLAY one node — re-derive it from the signed forest, pure
    and provider-agnostic (T-I2: the reconstruction byte-matches the original).

    Replay is NOT LLM re-execution (which is stochastic and would couple the audit
    path to a provider); it is byte-exact reconstruction of the recorded, signed
    envelope via the canonical round-trip — which is WHY it is deterministic.
    Integrity is checked first: replay requires a positively VERIFIED forest — any
    chain that is NOT VERIFIED is REFUSED, including a DEGRADED (keyless / downgraded)
    one, so replay can never silently launder a tampered OR downgraded envelope. A
    keyless scheme (``sha256-placeholder``) is recomputable by any attacker with
    filesystem write access, so "not BROKEN" is NOT proof of authenticity — only
    VERIFIED (real-key HMAC / signature) is (Exogenous-Anchor Law — the anchor is the
    byte-stable reconstruction + the real-key signature, not a second runner).
    """
    if node_id not in forest.nodes:
        raise IdrForestError(f"replay_deterministic: unknown node {node_id!r}")
    if verify_chain_integrity(forest) is not Verdict.VERIFIED:
        raise IdrForestError(
            "replay_deterministic: forest is not VERIFIED — refusing to replay a chain "
            "that is not positively signature-verified (a DEGRADED/keyless chain is "
            "recomputable by an attacker, so it is refused, not trusted)"
        )
    return _round_trip_idr(forest.nodes[node_id].idr)


def replay_chain(forest: Forest, node_id: str) -> ReplayResult:
    """Replay the full signed chain root→``node_id`` — the deterministic, pure,
    provider-agnostic audit primitive. Returns the reconstructed trail, the
    integrity verdict, and a reproducible ``replay_digest``. No LLM: replay is data,
    not re-execution. See ``ReplayResult``."""
    if node_id not in forest.nodes:
        raise IdrForestError(f"replay_chain: unknown node {node_id!r}")
    verdict = verify_chain_integrity(forest)
    replayed = tuple(_round_trip_idr(n) for n in _ancestry_root_to_node(forest, node_id))
    digest = hashlib.sha256(
        "\n".join(_canonical_json(asdict(n)) for n in replayed).encode("utf-8")
    ).hexdigest()
    return ReplayResult(node_id=node_id, verdict=verdict, sequence=replayed, replay_digest=digest)


# ---------------------------------------------------------------------------
# Merkle commitment + inclusion proofs over the forest (selective
# disclosure for an audited record). Extends the RFC6962 primitive (lib.merkle, #3442) and the
# completeness-commitment wiring (#3453) to the IDR forest itself: prove ONE
# decision is committed by a single root WITHOUT revealing the others.
#
# Additive + forward-compatible (HAPPI "unknown fields forwarded unchanged",
# happi.md:85): these are pure read functions over an existing Forest — they
# do NOT change the Forest shape, add_idr, or signature verification.
# ---------------------------------------------------------------------------


class ForestInclusionError(IdrForestError):
    """Raised when an inclusion proof is requested for a node not in the forest."""


def _forest_leaves(forest: Forest) -> list[str]:
    """Sorted content-addresses of every forest node — the Merkle leaf set.

    Uses ``content_addr`` (ts/id/audit-free, "sha256:<hex>"), so the root commits
    to node CONTENT: tampering any node's semantic body changes its address and
    thus the root. Sorted ⇒ order-independent + deterministic across runs.
    """
    return sorted(content_addr(asdict(node.idr)) for node in forest.nodes.values())


def forest_merkle_root(forest: Forest) -> str:
    """RFC6962 Merkle root over the forest's node content-addresses. Empty forest
    ⇒ the canonical empty-tree root. Any node change moves the root."""
    return merkle_root([a.encode("utf-8") for a in _forest_leaves(forest)])


def forest_inclusion_proof(forest: Forest, node_id: str) -> dict:
    """Prove ONE node (by id) is committed by ``forest_merkle_root`` — without
    revealing the other nodes. Returns ``{node_id, content_addr, proof,
    forest_root, node_n}``. Raises ``ForestInclusionError`` if ``node_id`` is not
    a forest node (cannot prove a decision that was never recorded)."""
    node = forest.nodes.get(node_id)
    if node is None:
        raise ForestInclusionError(f"{node_id!r} is not a node of this forest")
    addr = content_addr(asdict(node.idr))
    leaves_sorted = _forest_leaves(forest)
    leaves = [a.encode("utf-8") for a in leaves_sorted]
    return {
        "node_id": node_id,
        "content_addr": addr,
        "proof": inclusion_proof(leaves, leaves_sorted.index(addr)),
        "forest_root": merkle_root(leaves),
        "node_n": len(leaves),
    }


def verify_forest_inclusion(content_addr_hex: str, proof, forest_root: str) -> bool:
    """True iff ``content_addr_hex`` + ``proof`` reproduce ``forest_root``. A third
    party verifies one decision's membership with ONLY that decision's content
    address + the ~log2(N) proof + the signed root — never the other decisions.
    Tamper / wrong proof / wrong root all fail (lib.merkle.verify_inclusion)."""
    return verify_inclusion(content_addr_hex.encode("utf-8"), proof, forest_root)


# ---------------------------------------------------------------------------
# P3 — Forest edit-distance metric + contraction proof harness
# ---------------------------------------------------------------------------


def forest_edit_distance(a: Forest, b: Forest) -> float:
    """Symmetric distance between two forests over [0, 1].

    Computes the Jaccard distance over the set of node content-addresses:
    ``1 - |A ∩ B| / |A ∪ B|``.  The distance is:
    * **0** iff both forests have the same nodes with identical content.
    * **1** iff the forests share no nodes.
    * **monotone non-increasing** under ``canonicalise`` iterations because
      canonicalisation preserves node content (idempotent) and never adds
      or removes nodes — so the intersection never shrinks and the union
      never grows across applications.

    Pure — no side effects, no I/O.
    """
    addrs_a = set(_forest_leaves(a))
    addrs_b = set(_forest_leaves(b))
    union = addrs_a | addrs_b
    if not union:
        return 0.0
    intersection = addrs_a & addrs_b
    return 1.0 - (len(intersection) / len(union))
