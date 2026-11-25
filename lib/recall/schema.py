"""Recall substrate — shared data contract (the cross-track pin).

Every module in lib/recall/ (index, mmr, preassert, forensic, cards,
dejavu, wizard, provenance_card, crossharness_demo) codes against THIS
file. Pure stdlib, no GRIP imports — mirrors lib/ultracode kernel
discipline so the contract is portable across harnesses.

Locked design (HITL decision matrix, 2026-07-17, plans/
grip-recall-substrate.ultraprompt.md §Decisions Locked):
  F1 subject index primary + MMR audit commitment (both in v1)
  F2 DENY -> error state + forced-surface-to-operator + signed RED-chain
     forensic node; autonomous contexts hard-block + record, never silent
  F3 coverage = mediated surfaces only, honestly labelled; thin adapters
     over one harness-agnostic syscall
  F4 incremental event-append + verify-hash-on-read; MMR root per batch
  F5 semantic/fuzzy search NEVER alone authorises a negative claim
  F6 all four delight surfaces, built on three parallel tracks

Shared function signatures (implemented in sibling modules, pinned here):
  index.query_subject(subject, type=None, store=None) -> QueryResult
  index.verify_artefact(artefact, store=None) -> dict  # {ok, checks:{...}}
  index.append_event(event, store=None) -> str          # event_id
  preassert.preassert(claim, scope=None, store=None) -> PreassertResult
  mmr.append_leaf(leaf_hash, state) -> state ; mmr.root(state) -> str
  mmr.prove(state, position) -> list ; mmr.verify(root, leaf, proof) -> bool
  forensic.emit(node, store=None) -> ForensicNode  # falsifier-check first
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
from dataclasses import asdict, dataclass, field

# ---------------------------------------------------------------- verdicts
VERDICT_ALLOW = "ALLOW"
VERDICT_DENY = "DENY"
VERDICT_UNVERIFIABLE = "UNVERIFIABLE"
VERDICTS = (VERDICT_ALLOW, VERDICT_DENY, VERDICT_UNVERIFIABLE)

# artefact lifecycle status (F4: supersession/invalidation are first-class)
STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_INVALIDATED = "invalidated"
STATUSES = (STATUS_ACTIVE, STATUS_SUPERSEDED, STATUS_INVALIDATED)

# append-only index event kinds
EVENT_ARTEFACT_SEEN = "artefact_seen"
EVENT_SUPERSEDE = "supersede"
EVENT_INVALIDATE = "invalidate"
EVENT_ALIAS_ADD = "alias_add"
EVENT_KINDS = (
    EVENT_ARTEFACT_SEEN,
    EVENT_SUPERSEDE,
    EVENT_INVALIDATE,
    EVENT_ALIAS_ADD,
)

# forensic (RED chain) severity + interception point
SEVERITY_GATED_DECISION = "gated_decision"
SEVERITY_PRE_OUTPUT = "pre_output"
SEVERITY_REACHED_USER = "reached_user"
SEVERITIES = (SEVERITY_GATED_DECISION, SEVERITY_PRE_OUTPUT, SEVERITY_REACHED_USER)

CAUGHT_OUTPUT_GATE = "output_gate"
CAUGHT_TOOL_RESULT_GATE = "tool_result_gate"
CAUGHT_SYSCALL_WRAPPER = "syscall_wrapper"
CAUGHT_MANUAL = "manual"
WHERE_CAUGHT = (
    CAUGHT_OUTPUT_GATE,
    CAUGHT_TOOL_RESULT_GATE,
    CAUGHT_SYSCALL_WRAPPER,
    CAUGHT_MANUAL,
)

# The ONLY identity dimensions a node may carry in plaintext for report
# aggregation (council seal 970973d5: publish-safe dims = model+provider +
# harness ONLY; session/user/IP-derived stay machine-local as the HMAC'd
# forensic_uuid + field mask). Enforced fail-closed at build_node time.
PUBLISH_DIM_KEYS = ("model", "provider", "harness")


# ------------------------------------------------------------ canonical hash
def canonical_json(obj) -> str:
    """Deterministic JSON — the byte form every hash in the substrate uses."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(obj) -> str:
    """sha256 over canonical JSON. MMR leaves, result hashes, event ids."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def forensic_uuid(identity: dict, key: bytes) -> str:
    """HMAC-SHA256 over the identity tuple (PII-safe by construction).

    identity carries whatever subset is known of: session_id, prompt_id,
    user, model, provider, happiverse_node, harness, envelope_id, ip,
    serial. The raw tuple never enters the chain — only this HMAC plus a
    field-presence mask, so the RED chain stays publishable evidence
    without leaking identity (rules/no-sensitive-info-in-grip.md).
    """
    mask = sorted(k for k, v in identity.items() if v not in (None, ""))
    payload = canonical_json({"fields": mask, "identity": identity})
    return _hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- artefacts
@dataclass(frozen=True)
class Artefact:
    """One recallable thing the moat knows about (a lookup result row)."""

    subject: str
    type: str  # e.g. render | receipt | doc | code | record
    path: str  # on-disk pointer (may be file-history blob path)
    blob_hash: str  # sha256 of file content at index time ("" if unhashed)
    created_at: str  # ISO-8601 UTC
    status: str = STATUS_ACTIVE  # one of STATUSES
    idr_id: str | None = None  # green-chain leaf that recorded it
    receipt_path: str | None = None
    receipt_hash: str | None = None
    supersedes: str | None = None  # event_id this artefact superseded
    invalidated_by: str | None = None  # event_id that invalidated it
    source: str = "index"  # idr | context | receipt | file-history | index

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Artefact":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})  # type: ignore[arg-type]


@dataclass(frozen=True)
class IndexEvent:
    """Append-only event-log entry; its content_hash is the MMR leaf (F4)."""

    kind: str  # one of EVENT_KINDS
    ts: str  # ISO-8601 UTC
    subject: str
    aliases: tuple = ()
    artefact: dict | None = None  # Artefact.to_dict() for artefact_seen
    target_event_id: str | None = None  # for supersede/invalidate
    note: str = ""

    def leaf(self) -> str:
        return content_hash(
            {
                "kind": self.kind,
                "ts": self.ts,
                "subject": self.subject,
                "aliases": list(self.aliases),
                "artefact": self.artefact,
                "target_event_id": self.target_event_id,
                "note": self.note,
            }
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["aliases"] = list(self.aliases)
        d["event_id"] = self.leaf()
        return d


@dataclass(frozen=True)
class Freshness:
    """Proof-of-currency carried by every verdict (F4 verify-on-read)."""

    verified: bool
    index_head: str  # MMR root at read time
    event_count: int
    checked_at: str  # ISO-8601 UTC
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class QueryResult:
    """grip-recall query output — identical bytes on every harness (T3 demo)."""

    subject: str
    artefacts: tuple = ()  # tuple[Artefact-dicts] latest-event-wins ordered
    freshness: dict | None = None  # Freshness.to_dict()
    inclusion_proof: dict | None = None  # {leaf, position, proof[], root}

    @property
    def result_hash(self) -> str:
        # Hash ONLY the artefact content — freshness.checked_at is wall-clock
        # and would break cross-harness identical-result comparison.
        return content_hash({"subject": self.subject, "artefacts": list(self.artefacts)})

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "artefacts": list(self.artefacts),
            "freshness": self.freshness,
            "inclusion_proof": self.inclusion_proof,
            "result_hash": self.result_hash,
        }


@dataclass(frozen=True)
class PreassertResult:
    """Verdict on a claim. F5: a DENY-grade or ALLOW-grade NEGATIVE claim
    requires explicit scope + verified freshness + exact/alias/FTS basis —
    semantic similarity may only ADD evidence, never subtract to zero."""

    verdict: str  # one of VERDICTS
    claim: str
    scope: tuple = ()  # explicit scope the claim was checked against
    evidence: tuple = ()  # tuple[Artefact-dicts] contradicting/supporting
    freshness: dict | None = None
    basis: str = "exact"  # exact | alias | fts (never "semantic" alone)
    reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scope"] = list(self.scope)
        d["evidence"] = list(self.evidence)
        return d


# ------------------------------------------------------------- RED chain
@dataclass(frozen=True)
class ForensicNode:
    """One signed witness record of a proven-incorrect assertion.

    Witness evidence, not a program: signed, sealed, append-only,
    non-replayable. MUST NOT be sealed unless falsifier_survived is True —
    an accusation that has not survived its own falsification attempt is
    forbidden (libel-generator guard). The remediation_edge back to the
    green chain is what makes the RED chain useful for RSI, not shameful.
    """

    forensic_uuid: str  # HMAC over identity tuple (see forensic_uuid())
    ts: str
    claim_verbatim: str
    claim_source: str  # assistant_output | tool_conclusion
    evidence: tuple = ()  # tuple[dict]: {kind, path, hash, idr_id}
    belief_head: str = ""  # context-chain latest record id at assertion time
    toolchain_trace: tuple = ()  # digests of tool results preceding the claim
    verifier: dict = field(default_factory=dict)  # {index_head, ruleset_hash, alias_hash}
    severity: str = SEVERITY_PRE_OUTPUT  # one of SEVERITIES
    where_caught: str = CAUGHT_OUTPUT_GATE  # one of WHERE_CAUGHT
    falsifier: str = ""  # what would make this accusation wrong
    falsifier_survived: bool = False  # sealed only when True
    falsifier_detail: str = ""
    remediation_edge: str | None = None  # green-chain IDR id of the fix
    identity_mask: tuple = ()  # which identity fields fed the HMAC
    input_commitments: tuple = ()  # sha256 commitments of raw inputs (prompt,
    # context) — reveal-and-verify in a dispute without storing raw content
    # (council seal 8acc9c14: "raw input commitments")
    anchor_ref: str | None = None  # external temporal-anchor pointer (OTS
    # batch id) set when the RED root joins the green root's anchoring
    # cadence (council seal 8acc9c14: "cryptographic temporal anchoring")
    publish_dims: dict = field(default_factory=dict)  # publish-safe
    # aggregation dims ONLY — keys restricted to PUBLISH_DIM_KEYS at
    # build_node time (council seal 970973d5). Pre-extension nodes lack
    # the field entirely; verify_chain recomputes signatures over the
    # dict as READ from disk, so old sealed lines verify unchanged.
    prev_hash: str = ""  # hash-chain linkage within the RED chain
    sig: str = ""  # HMAC over canonical node minus sig

    def signable(self) -> dict:
        d = asdict(self)
        d.pop("sig", None)
        for k in ("evidence", "toolchain_trace", "identity_mask", "input_commitments"):
            d[k] = list(d[k])
        return d

    def to_dict(self) -> dict:
        d = self.signable()
        d["sig"] = self.sig
        return d


NEGATIVE_EXISTENCE_HINT = (
    "A negative-existence claim is ALLOWED only when (a) the checked scope "
    "is explicit AND (b) index freshness is verified. Otherwise the honest "
    "output is 'I did not find it in <scope>', never 'it does not exist.'"
)
