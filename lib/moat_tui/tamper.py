"""moat_tui.tamper — FALSIFICATION: mutation plus RE-VERIFICATION.

The delight surface. The user becomes the forger: pick a vector, edit the record,
and watch the legs cascade green -> red under the **shipped** verifiers.

**EVERY RED IS EARNED.** Nothing here decides a verdict. Each mutator hands its
mutated copy straight back to the same function that produced the green a moment
earlier — ``lib.idr_forest.verify_chain_integrity`` (via ``model.verify_idr_leg``
/ ``model.verify_belief_leg``), ``lib.prove_it.render`` (via
``model.verify_cite_leg``), and ``lib.der_receipt.verify_receipt``. A simulated
failure would make the whole tool theatre, so there is no code path that can
report a break without a real verifier having said so.

**NOTHING IS EVER WRITTEN.** Mutation is ``copy.deepcopy`` + ``dataclasses.replace``
on in-memory copies. This module never calls ``append_idr``, ``checkpoint``,
``append_context``, ``record_proveit_provenance``, or ``build_receipt`` with
stamping, and never opens a ledger for writing. ``reset_tamper`` is therefore
instant and total — there is no residue on disk to undo.

**FUNCTIONAL, NOT MUTATING.** Every entry point returns a NEW ``TamperSession``,
so ``reset`` is free and the full before/after history stays intact for rendering.

**THE PINCER** (reproduced live against a real sealed council record, and the
reason the demo lands): the belief-state is *inside* the signed payload, so

===========================  =======  ============  =====
step                         fixity   authenticity  joint
===========================  =======  ============  =====
baseline                     OK       OK            OK
1 edit the belief-state      FAIL     OK            FAIL
2 repair the hash            OK       **FAIL**      FAIL
3 repair the belief binding  OK       FAIL          OK
===========================  =======  ============  =====

Watch the authenticity column. It survives step 1 — the forger looks fine. The
moment they repair the hash to cover their tracks, authenticity dies, and no
further repair revives it: that would need the Ed25519 private key, which the
receipt does not contain.

**THE HONEST LIMIT.** One vector, ``der_resign``, SUCCEEDS — it repairs the
receipt and re-signs it with a fresh keypair, and every gating check on that
receipt goes green again, because the verifier loads the public key *from the
receipt* and pins no signer identity. It ships flagged ``honest_limit=True`` and
captioned, because a demo that only ever goes red is advertising, not evidence.
The claim is scoped to the DER receipt: re-signing repairs nothing in the IDR,
BELIEF or CITE ledgers, so those stay red if they were forged too.
"""
from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# --------------------------------------------------------------------------
# Import bootstrap — mirrors model.py. The moat libraries use BOTH
# ``from lib.x import y`` (repo root on sys.path) and bare ``from x import y``
# (lib/ on sys.path), so both entries are mandatory. Done here as well as in
# model.py so import ORDER cannot matter.
# --------------------------------------------------------------------------
_PKG_DIR = Path(__file__).resolve().parent          # .../lib/moat_tui
_LIB_DIR = _PKG_DIR.parent                          # .../lib
_REPO_ROOT = _LIB_DIR.parent                        # repo root
for _p in (str(_REPO_ROOT), str(_LIB_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The DER primitives. Safe to import at module scope even WITHOUT
# ``cryptography``: lib.der_receipt guards that import itself and simply sets
# ``_HAVE_ED25519 = False``. Only key GENERATION needs the dependency, so it is
# imported lazily inside ``forge_resign``.
#
# ``_memory_head`` and ``_b64e`` are underscore-private but are the ONLY
# canonical implementations. Re-deriving either would be reimplementation: the
# forger's repairs must be computed the way the verifier computes them, or the
# pincer would be a strawman that breaks for the wrong reason.
from lib.der_receipt import (  # noqa: E402
    _b64e,
    _memory_head,
    canonical,
    sha256_hex,
    verify_receipt,
)
from lib.precog.idr import PrecogIDR  # noqa: E402

from .model import (  # noqa: E402
    STATUS_BROKEN,
    STATUS_DEGRADED,
    STATUS_NO_DATA,
    STATUS_UNAVAILABLE,
    STATUS_VERIFIED,
    BeliefScope,
    DecisionView,
    ForestBuild,
    LegName,
    LegResult,
    MoatPaths,
    _der_status,
    build_der_receipt,
    build_idr_forest,
    crypto_available,
    key_present,
    resolve_paths,
    verify_belief_leg,
    verify_cite_leg,
    verify_idr_leg,
)

# Forged values. Deliberately self-announcing: a reader who sees one of these on
# screen must never wonder whether it came from the real ledger.
FORGED_BELIEF = "FORGED: this belief was never held"
FORGED_MODEL = "FORGED-MODEL-NEVER-CONSULTED"
FORGED_VERDICT = "FORGED: this verdict was never reached"
FORGED_FINGERPRINT = "forged-fingerprint"
FORGED_QUOTE = "FORGED: this sentence appears in no source"


# --------------------------------------------------------------------------
# The forger's catalogue
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TamperVector:
    """One forge the user can attempt.

    ``target`` selects the mutator; ``step`` sub-selects within the DER pincer.
    ``honest_limit`` marks a vector that SUCCEEDS — rendered in its own style
    rather than hidden, because the boundary is the evidence.
    """

    key: str
    label: str
    target: str
    caption: str
    honest_limit: bool = False
    step: str = ""
    field: str = ""
    value: str = ""


VECTORS: tuple[TamperVector, ...] = (
    TamperVector(
        key="1", label="belief_edit", target="belief", field="summary",
        value=FORGED_BELIEF,
        caption="Rewrite what the AI believed at this moment. The seal over that "
                "belief stops matching and the fingerprint committing the whole "
                "set of beliefs moves.",
    ),
    TamperVector(
        key="2", label="idr_fingerprint", target="idr", field="fingerprint",
        value=FORGED_FINGERPRINT,
        caption="Flip one decision's fingerprint in the ledger. That record stops "
                "verifying and the ledger's overall root moves with it.",
    ),
    TamperVector(
        key="3", label="cite_quote", target="cite", value=FORGED_QUOTE,
        caption="Replace a quoted passage with one that appears in no source. The "
                "citation checker looks for it verbatim and cannot find it.",
    ),
    TamperVector(
        key="4", label="der_edit_belief", target="der", step="edit_belief",
        caption="THE PINCER, step 1 — swap which model decided. Fixity and the "
                "belief binding fail; the signature still passes, so the forger "
                "looks fine so far.",
    ),
    TamperVector(
        key="5", label="der_repair_fixity", target="der", step="repair_fixity",
        caption="Step 2 — recompute the hash to cover the edit. Fixity goes green "
                "again, and in the same instant the signature dies.",
    ),
    TamperVector(
        key="6", label="der_repair_joint", target="der", step="repair_joint",
        caption="Step 3 — repair the belief binding too. Two legs green, and the "
                "signature is still invalid. Repairing it needs the private key.",
    ),
    TamperVector(
        key="7", label="der_resign", target="der", step="resign", honest_limit=True,
        caption="Repair the forgery and sign it with a BRAND NEW keypair. Every "
                "check on this receipt goes green — it proves it was signed by "
                "whoever holds the key it embeds, not WHO that is. Identity is "
                "out-of-band. Any forged decision or belief stays red.",
    ),
    TamperVector(
        key="r", label="reset", target="reset",
        caption="Back to pristine. Nothing was written, so there is nothing to undo.",
    ),
)


def vector_by_key(key: str) -> TamperVector | None:
    """Keypress -> vector. Returns None for an unknown key rather than raising,
    so a stray keystroke can never crash the interaction loop."""
    token = (key or "").strip().lower()
    for vector in VECTORS:
        if vector.key == token or vector.label == token:
            return vector
    return None


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TamperStep:
    """One applied vector, with the four-leg verdict either side of it."""

    label: str
    caption: str = ""
    before: tuple[LegResult, ...] = ()
    after: tuple[LegResult, ...] = ()
    merkle_before: str = ""
    merkle_after: str = ""
    honest_limit: bool = False
    key: str = ""


@dataclass(frozen=True)
class TamperSession:
    """The forger's desk: the mutable subjects, the pristine baseline, the history.

    ``origin`` holds the PRISTINE subjects so ``reset_tamper`` restores them.
    Without it, resetting would clear the visible history while silently leaving
    the tampered subjects in place — and the next forge would compound on top of
    the previous one.
    """

    subject_id: str
    baseline: tuple[LegResult, ...] = ()
    steps: tuple[TamperStep, ...] = ()
    scope: BeliefScope | None = None
    build: ForestBuild | None = None
    receipt: Mapping[str, Any] | None = None
    spec: Mapping[str, Any] | None = None
    idr: PrecogIDR | None = None
    have_key: bool = True
    origin: Mapping[str, Any] | None = None
    vectors: tuple[TamperVector, ...] = VECTORS

    @property
    def legs(self) -> tuple[LegResult, ...]:
        """The CURRENT four-leg verdict — the last step's, else the baseline."""
        return self.steps[-1].after if self.steps else self.baseline


# --------------------------------------------------------------------------
# Re-verification — every leg re-runs a shipped verifier
# --------------------------------------------------------------------------


def _der_leg(receipt: Mapping[str, Any] | None, *, offline: bool = True) -> LegResult:
    """Re-verify a (possibly mutated) receipt through the shipped
    ``verify_receipt``.

    ``check_anchor`` is off by default — it otherwise shells out to the
    OpenTimestamps calendar over the network. Reports ``unavailable`` when
    ``cryptography`` is missing, because a failed Ed25519 leg is otherwise
    indistinguishable from a forged signature.

    The green/red mapping is ``model._der_status`` — the SAME mapper
    ``model.verify_der_leg`` uses for the untampered baseline. Re-deriving the
    ternary here would put a second, drifting definition of "verified" behind the
    tamper column, which is precisely the thing this module promises it does not
    do. It differs from ``verify_der_leg`` only in taking a RECEIPT rather than a
    record, because a mutated receipt must be re-verified, never rebuilt.
    """
    if receipt is None:
        return LegResult(LegName.DER, STATUS_NO_DATA, "no DER receipt")
    if not crypto_available():
        return LegResult(
            LegName.DER, STATUS_UNAVAILABLE, "crypto unavailable",
            note="cryptography (Ed25519) not installed — cannot verify, "
                 "NOT a failed signature",
        )
    result = verify_receipt(dict(receipt), check_anchor=not offline)
    return LegResult(
        LegName.DER,
        _der_status(result),
        "ok" if result.get("ok") else "FAILED",
        dict(result.get("checks") or {}),
        note="timestamp is informational — ok gates on fixity + authenticity "
             "+ joint-causation",
    )


def _verify_all(session: TamperSession) -> tuple[LegResult, ...]:
    """The four legs, recomputed from the session's CURRENT subjects.

    Baseline and every step go through THIS one function, so a delta can only
    reflect a real change of verdict — never a formatting difference between two
    ways of computing the same leg.
    """
    idr_leg = (
        verify_idr_leg(session.build, have_key=session.have_key)
        if session.build is not None
        else LegResult(LegName.IDR, STATUS_NO_DATA, "no decision ledger")
    )
    belief_leg = (
        verify_belief_leg(session.scope, have_key=session.have_key)
        if session.scope is not None
        else LegResult(LegName.BELIEF, STATUS_NO_DATA, "no belief scope")
    )
    return (idr_leg, belief_leg,
            verify_cite_leg(session.idr, session.spec),
            _der_leg(session.receipt))


def _merkle(legs: Sequence[LegResult], name: str) -> str:
    """The short Merkle root of one leg — the headline number the pane watches."""
    for leg in legs:
        if leg.name == name:
            root = leg.detail.get("merkle_root") if isinstance(leg.detail, Mapping) else ""
            return str(root or "")[:16]
    return ""


# The leg whose Merkle root a vector moves. DER and CITE commit no forest, so
# they report no root and ``render._merkle_row`` omits the line entirely.
_MERKLE_LEG: Mapping[str, str] = {"belief": LegName.BELIEF, "idr": LegName.IDR}


# --------------------------------------------------------------------------
# Mutators — in-memory only, each returning (mutated subject, re-verified leg)
# --------------------------------------------------------------------------


def _pick_belief_node(nodes: Sequence[PrecogIDR], field: str) -> int:
    """Index of the most recent belief node carrying ``field`` — else the last."""
    for i in range(len(nodes) - 1, -1, -1):
        decision = nodes[i].decision
        if isinstance(decision, Mapping) and field in decision:
            return i
    return len(nodes) - 1


def tamper_belief_scope(
    scope: BeliefScope,
    *,
    field: str = "summary",
    value: str = FORGED_BELIEF,
    have_key: bool = True,
) -> tuple[BeliefScope, LegResult]:
    """Edit one belief node, then RE-VERIFY through the shipped chain builder.

    The decision dict is DEEP-COPIED before mutation: ``dataclasses.replace``
    copies field references, so writing into the original dict in place would
    corrupt the pristine record and make ``reset`` a lie.

    Reproduced live: a VERIFIED 5-node scope went BROKEN and its Merkle root
    moved ``bc613559da7b2154`` -> ``be74b85be622f1f7``.
    """
    nodes = list(scope.nodes)
    if not nodes:
        return scope, verify_belief_leg(scope, have_key=have_key)
    index = _pick_belief_node(nodes, field)
    decision = copy.deepcopy(dict(nodes[index].decision or {}))
    decision[field] = value
    nodes[index] = replace(nodes[index], decision=decision)
    mutated = replace(scope, nodes=tuple(nodes))
    return mutated, verify_belief_leg(mutated, have_key=have_key)


def tamper_idr_forest(
    build: ForestBuild,
    node_id: str = "",
    *,
    field: str = "fingerprint",
    value: str = FORGED_FINGERPRINT,
    have_key: bool = True,
) -> tuple[ForestBuild, LegResult]:
    """Replace one field on one decision record and REBUILD the forest.

    Records are re-fed in ``forest.nodes`` INSERTION order, which is the original
    file order — already topological. Re-sorting (e.g. by depth) could place a
    child before its parent and trigger a rejection cascade that would look like
    corruption caused by the tamper rather than by the rebuild.

    An unknown ``field`` returns the forest untouched instead of raising, so a
    bad vector definition cannot crash the interaction loop.
    """
    records = [node.idr for node in build.forest.nodes.values()]
    if not records or field not in PrecogIDR.__dataclass_fields__:
        return build, verify_idr_leg(build, have_key=have_key)
    target = node_id if any(r.id == node_id for r in records) else records[0].id
    mutated_records = [
        replace(rec, **{field: value}) if rec.id == target else rec for rec in records
    ]
    mutated = build_idr_forest(mutated_records, build.roots)
    return mutated, verify_idr_leg(mutated, have_key=have_key)


def tamper_cite_spec(
    spec: Mapping[str, Any], *, value: str = FORGED_QUOTE
) -> tuple[Mapping[str, Any], LegResult]:
    """Corrupt a citation's quote, then RE-RUN the shipped ``prove_it.render``.

    The read-only ``render`` is what re-checks the quote against its source —
    never ``main()`` or ``build_legal_receipt``, which default to WRITING the
    live ledgers.
    """
    mutated = copy.deepcopy(dict(spec))
    citations = mutated.get("citations")
    if isinstance(citations, list) and citations and isinstance(citations[0], dict):
        citations[0]["quote"] = value
    return mutated, verify_cite_leg(None, mutated)


def _der_belief(receipt: dict) -> dict:
    return receipt.setdefault("decision", {}).setdefault("belief_state", {})


def _der_edit_belief(receipt: dict) -> None:
    """Swap WHICH model decided — the belief-state joint-causation protects."""
    belief = _der_belief(receipt)
    speakers = belief.get("speakers")
    if isinstance(speakers, list) and speakers and isinstance(speakers[0], dict):
        speakers[0]["model"] = FORGED_MODEL
    else:  # a record with no speakers — forge the verdict instead
        receipt.setdefault("decision", {})["verdict"] = FORGED_VERDICT


def _der_repair_fixity(receipt: dict) -> None:
    """Recompute the payload hash over the canonical decision — the forger's
    cover-up, and the exact move that invalidates the signature."""
    receipt["payload_sha256"] = sha256_hex(canonical(receipt.get("decision", {})))


def _der_repair_joint(receipt: dict) -> None:
    """Recompute the belief binding from its three REAL inputs, then re-repair
    fixity — changing ``memory_head`` changes the decision, which would otherwise
    break the hash the forger just fixed."""
    belief = _der_belief(receipt)
    belief["memory_head"] = _memory_head(
        belief.get("speakers"), belief.get("judge"),
        receipt.get("decision", {}).get("sealed_sha256"),
    )
    _der_repair_fixity(receipt)


_DER_STEPS: Mapping[str, Callable[[dict], None]] = {
    "edit_belief": _der_edit_belief,
    "repair_fixity": _der_repair_fixity,
    "repair_joint": _der_repair_joint,
}


def tamper_der_receipt(
    receipt: Mapping[str, Any], step: str
) -> tuple[dict, LegResult]:
    """One step of THE PINCER, re-verified by the shipped ``verify_receipt``.

    Repairs use the shipped ``canonical`` / ``sha256_hex`` / ``_memory_head``, so
    the forger's fixes are genuine rather than strawmen that fail for the wrong
    reason. An unknown step mutates nothing and simply re-reports the receipt.
    """
    mutated = copy.deepcopy(dict(receipt))
    handler = _DER_STEPS.get(step)
    if handler is not None:
        handler(mutated)
    return mutated, _der_leg(mutated)


def forge_resign(receipt: Mapping[str, Any]) -> tuple[dict, LegResult]:
    """THE HONEST LIMIT — a COMPLETE forgery: repair, then re-sign with a fresh
    keypair, and watch every gating check on the receipt pass again.

    ``verify_receipt`` loads the public key FROM THE RECEIPT and pins no signer
    identity, so a forger who regenerates the pair verifies clean. Shipping this
    is what makes the other vectors evidence instead of advertising: the receipt
    proves it was signed by whoever holds the private key for its embedded public
    key — it does not prove WHO that is. Identity is out-of-band.

    It repairs the belief binding and the payload hash FIRST — which is what a
    forger holding a fresh key would actually do, and what makes the claim true
    from ANY prior state. Signing alone is not enough: re-signing a receipt whose
    hash still disagrees with its decision leaves fixity and joint-causation red,
    so the vector would advertise an all-green it had not produced whenever the
    user pressed it out of sequence. Both repairs are no-ops on an already-
    consistent receipt, so pressing this on a pristine record still demonstrates
    the point — a genuine record re-signed by an unknown key verifies clean.
    """
    mutated = copy.deepcopy(dict(receipt))
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    except ImportError:
        return mutated, LegResult(
            LegName.DER, STATUS_UNAVAILABLE, "crypto unavailable",
            note="cryptography (Ed25519) not installed — cannot re-sign",
        )
    _der_repair_joint(mutated)  # recomputes memory_head, then payload_sha256
    private = Ed25519PrivateKey.generate()
    payload = str(mutated.get("payload_sha256") or "")
    mutated["signature"] = {
        "alg": "ed25519",
        "sig_b64": _b64e(private.sign(payload.encode("ascii"))),
        "pubkey_b64": _b64e(
            private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ),
    }
    return mutated, _der_leg(mutated)


# --------------------------------------------------------------------------
# Session-level mutation
# --------------------------------------------------------------------------


def _apply_belief(session: TamperSession, vector: TamperVector) -> tuple[TamperSession, str]:
    if session.scope is None or not session.scope.nodes:
        return session, "no belief scope loaded — nothing to forge"
    scope, _leg = tamper_belief_scope(
        session.scope, field=vector.field or "summary",
        value=vector.value or FORGED_BELIEF, have_key=session.have_key,
    )
    return replace(session, scope=scope), ""


def _apply_idr(session: TamperSession, vector: TamperVector) -> tuple[TamperSession, str]:
    if session.build is None or not session.build.forest.nodes:
        return session, "no decision ledger loaded — nothing to forge"
    build, _leg = tamper_idr_forest(
        session.build, session.subject_id, field=vector.field or "fingerprint",
        value=vector.value or FORGED_FINGERPRINT, have_key=session.have_key,
    )
    return replace(session, build=build), ""


def _apply_cite(session: TamperSession, vector: TamperVector) -> tuple[TamperSession, str]:
    if not session.spec:
        return session, "no citation spec attached to this decision — nothing to forge"
    spec, _leg = tamper_cite_spec(session.spec, value=vector.value or FORGED_QUOTE)
    return replace(session, spec=spec), ""


def _apply_der(session: TamperSession, vector: TamperVector) -> tuple[TamperSession, str]:
    if session.receipt is None:
        return session, "no DER receipt for this decision — nothing to forge"
    if vector.step == "resign":
        receipt, _leg = forge_resign(session.receipt)
    else:
        receipt, _leg = tamper_der_receipt(session.receipt, vector.step)
    return replace(session, receipt=receipt), ""


_MUTATORS: Mapping[str, Callable[[TamperSession, TamperVector], tuple[TamperSession, str]]] = {
    "belief": _apply_belief,
    "idr": _apply_idr,
    "cite": _apply_cite,
    "der": _apply_der,
}


def start_tamper(
    view: DecisionView | None = None,
    scope: BeliefScope | None = None,
    record: Mapping[str, Any] | None = None,
    *,
    build: ForestBuild | None = None,
    spec: Mapping[str, Any] | None = None,
    receipt: Mapping[str, Any] | None = None,
    have_key: bool | None = None,
    paths: MoatPaths | None = None,
) -> TamperSession:
    """Open a session by capturing the PRISTINE four-leg baseline.

    Baseline capture must precede any mutation — it is the "before" column every
    step is diffed against. The DER receipt is built ONCE here, offline
    (``do_stamp=False``), so no keypress ever touches the network.

    ``have_key`` defaults to a real probe rather than to ``True``: assuming a key
    that is absent would let ``_ensure_signing_key`` generate a fresh random one,
    turning every record BROKEN and manufacturing exactly the false tamper alarm
    this package exists to prevent.
    """
    if have_key is None:
        have_key = key_present(paths or resolve_paths())
    if receipt is None and record is not None:
        receipt = build_der_receipt(record)
    if scope is None and view is not None:
        scope = view.scope
    session = TamperSession(
        subject_id=(view.row.node_id if view is not None else ""),
        scope=scope, build=build, receipt=receipt, spec=spec,
        idr=(view.idr if view is not None else None),
        have_key=bool(have_key),
    )
    session = replace(
        session,
        origin={"scope": scope, "build": build, "spec": spec,
                "receipt": copy.deepcopy(dict(receipt)) if receipt is not None else None},
    )
    return replace(session, baseline=_verify_all(session))


def apply_vector(session: TamperSession, key: str) -> TamperSession:
    """Mutate, re-verify through the REAL functions, and append one step.

    Applying the pincer as SEPARATE keypresses is what makes it land: the user
    watches one leg go green exactly as the next one gives way.
    """
    vector = vector_by_key(key)
    if vector is None:
        return session
    if vector.target == "reset":
        return reset_tamper(session)
    mutator = _MUTATORS.get(vector.target)
    if mutator is None:
        return session
    before = session.steps[-1].after if session.steps else session.baseline
    mutated, note = mutator(session, vector)
    after = _verify_all(mutated)
    leg = _MERKLE_LEG.get(vector.target, "")
    step = TamperStep(
        label=vector.label,
        caption=vector.caption + (f"  [{note}]" if note else ""),
        before=tuple(before), after=tuple(after),
        merkle_before=_merkle(before, leg) if leg else "",
        merkle_after=_merkle(after, leg) if leg else "",
        honest_limit=vector.honest_limit, key=vector.key,
    )
    return replace(mutated, steps=session.steps + (step,))


def reset_tamper(session: TamperSession) -> TamperSession:
    """Instant return to pristine — subjects AND history.

    Restoring the subjects is the load-bearing half: clearing only ``steps``
    would leave the tampered scope/forest/receipt in place, so the screen would
    claim pristine while the next forge compounded on the last one.
    """
    origin = session.origin or {}
    stored = origin.get("receipt")
    return replace(
        session,
        steps=(),
        scope=origin.get("scope", session.scope),
        build=origin.get("build", session.build),
        spec=origin.get("spec", session.spec),
        receipt=copy.deepcopy(dict(stored)) if stored is not None else session.receipt,
    )


def leg_delta(
    before: Sequence[LegResult], after: Sequence[LegResult]
) -> tuple[tuple[str, str, str], ...]:
    """``(leg, before_status, after_status)`` for CHANGED legs only.

    Returning only changes is what makes the visual read as a cascade rather than
    a wall of unchanged rows.
    """
    post = {leg.name: leg.status for leg in after}
    return tuple(
        (leg.name, leg.status, post[leg.name])
        for leg in before
        if leg.name in post and post[leg.name] != leg.status
    )


# --------------------------------------------------------------------------
# Plain English — WHY a leg broke, for a non-technical reader
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LegExplanation:
    """One leg's verdict, explained without jargon."""

    leg: str
    status_before: str
    status_after: str
    changed: bool
    plain: str


# The three gating sub-legs of a DER receipt, each with the plain-English reason
# it would fail. The timestamp leg is deliberately absent: it is informational
# and does not gate ``ok``, so quoting it as a failure would overstate the break.
_DER_REASONS: tuple[tuple[str, str], ...] = (
    ("fixity",
     "the decision text no longer matches the fingerprint stored beside it, so "
     "something was changed after it was sealed"),
    ("joint_causation",
     "the record of which models decided, and what they believed, no longer "
     "matches the summary that binds those beliefs to this decision"),
    ("authenticity",
     "the digital signature does not check out against the public key inside the "
     "receipt, and producing a valid one would need the private key, which the "
     "receipt does not contain"),
)

_BROKEN_PLAIN: Mapping[str, str] = {
    LegName.IDR:
        "A decision in the ledger was changed after it was signed. Its seal no "
        "longer matches its contents, and the single fingerprint covering the "
        "whole ledger has moved with it.",
    LegName.BELIEF:
        "The record of what the AI believed at this moment was altered. The seal "
        "over that belief no longer matches, and the fingerprint committing the "
        "whole set of beliefs has moved — so the change cannot be hidden.",
    LegName.CITE:
        "At least one quoted passage could not be found in the source it claims "
        "to come from.",
}

_VERIFIED_PLAIN: Mapping[str, str] = {
    LegName.IDR: "Every decision in the ledger still matches its seal.",
    LegName.BELIEF: "The beliefs recorded at this moment still match their seal.",
    LegName.CITE: "Every quoted passage was found, word for word, in its source.",
    LegName.DER:
        "All three gating checks pass: the text matches its hash, the signature "
        "is valid, and the beliefs are bound to the decision.",
}


def _der_plain(leg: LegResult) -> str:
    """Name the SPECIFIC sub-legs that failed, rather than a bare 'broken'."""
    detail = leg.detail if isinstance(leg.detail, Mapping) else {}
    failed = [text for key, text in _DER_REASONS if detail.get(key) is False]
    if not failed:
        return _VERIFIED_PLAIN[LegName.DER]
    return "This receipt fails because " + "; and ".join(failed) + "."


def explain_leg(leg: LegResult) -> str:
    """Plain English for ONE leg's current state — no jargon, no hashes.

    Grey states ("cannot verify") are spelled out as such. A tool that reported
    "cannot check" as "TAMPERED" would be lying in the most damaging direction.
    """
    status = leg.status
    if status == STATUS_NO_DATA:
        return ("There is nothing of this kind attached to this decision, so "
                "there is nothing to check.")
    if status == STATUS_UNAVAILABLE:
        reason = leg.note or "the information needed to check it is not available"
        return (f"This could NOT be checked, so nothing is claimed either way — "
                f"{reason}. This is not evidence of tampering.")
    if status == STATUS_DEGRADED:
        return ("This checked out, but with a weaker guarantee than a full pass — "
                "treat it as 'not disproved' rather than 'proved'.")
    if status == STATUS_BROKEN:
        return _der_plain(leg) if leg.name == LegName.DER else _BROKEN_PLAIN.get(
            leg.name, "This no longer matches the seal that was taken of it.")
    return (_der_plain(leg) if leg.name == LegName.DER
            else _VERIFIED_PLAIN.get(leg.name, "This still matches its seal."))


def _transition(before: str, after: str) -> str:
    """A leading clause naming the DIRECTION of a change, including a repair."""
    if before == after:
        return ""
    if after == STATUS_VERIFIED and before in (STATUS_BROKEN, STATUS_DEGRADED):
        return "This forge SUCCEEDED — the check passes again. "
    if after == STATUS_BROKEN:
        return "This broke under the real verifier. "
    return "This changed. "


def explain_legs(
    before: Sequence[LegResult], after: Sequence[LegResult]
) -> tuple[LegExplanation, ...]:
    """For EVERY leg, a plain-English account of where it now stands and why.

    Covers unchanged legs too: "the signature still holds" is exactly as
    load-bearing as "the hash broke" when the point is which guarantees survived
    the forge.
    """
    prior = {leg.name: leg.status for leg in before}
    out: list[LegExplanation] = []
    for leg in after:
        was = prior.get(leg.name, leg.status)
        out.append(LegExplanation(
            leg=leg.name,
            status_before=was,
            status_after=leg.status,
            changed=was != leg.status,
            plain=_transition(was, leg.status) + explain_leg(leg),
        ))
    return tuple(out)


def explain_session(session: TamperSession) -> tuple[LegExplanation, ...]:
    """The plain-English cascade for the session as it currently stands."""
    return explain_legs(session.baseline, session.legs)


def summarise_session(session: TamperSession) -> str:
    """What the forger attempted, what broke, and what stayed unforgeable.

    Emits identifiers, statuses and plain prose only — never belief text, prompt,
    speaker answers or judge synthesis, all of which the underlying records carry
    in full.
    """
    lines = [f"Forger's report for decision {session.subject_id or '(none)'}"]
    if not session.steps:
        lines.append("  Nothing forged yet — every leg is as it was found.")
    else:
        lines.append("  Attempted: " + ", ".join(s.label for s in session.steps))
    for item in explain_session(session):
        mark = "CHANGED" if item.changed else "unchanged"
        lines.append(f"  [{mark}] {item.leg}: {item.status_before} -> "
                     f"{item.status_after}")
        lines.append(f"      {item.plain}")
    if any(step.honest_limit for step in session.steps):
        lines.append(
            "  HONEST LIMIT: a forge succeeded because the receipt was re-signed "
            "with a brand new key. The receipt proves it was signed by whoever "
            "holds the key it embeds — not WHO that is. Identity is out-of-band.")
    lines.append("  Nothing was written: every mutation was in memory only.")
    return "\n".join(lines)


__all__ = [
    "FORGED_BELIEF",
    "FORGED_FINGERPRINT",
    "FORGED_MODEL",
    "FORGED_QUOTE",
    "FORGED_VERDICT",
    "VECTORS",
    "TamperVector",
    "TamperStep",
    "TamperSession",
    "LegExplanation",
    "vector_by_key",
    "start_tamper",
    "apply_vector",
    "reset_tamper",
    "tamper_belief_scope",
    "tamper_idr_forest",
    "tamper_cite_spec",
    "tamper_der_receipt",
    "forge_resign",
    "leg_delta",
    "explain_leg",
    "explain_legs",
    "explain_session",
    "summarise_session",
]
