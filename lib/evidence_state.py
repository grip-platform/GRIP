"""EvidenceState gate — first-class Belnap-Dunn evidence typing for agent claims.

Implements the formalism of the paper draft
``~/.grip/drafts/sunyata-architecture/PAPER-DRAFT-evidencestate-gate.md`` (\"The
EvidenceState Gate — closed-world values, open-world knowledge, and sealed
epistemic provenance\"), as shaped by the sealed council verdict sha256
``f32d93fcdbf64c22``, whose surviving folds bind this design:

- **C1** — suspension is POLICY, never FDE entailment. A blanket rule \"any
  LATENT or CONFLICT upstream suspends everything downstream\" would be less
  informative than the logic itself and would convert hallucination risk into
  unbounded abstention. Hence :func:`decide` is materiality-sensitive: the
  four-valued tables propagate what the evidence supports, and a separate,
  explicit policy layer chooses whether to act on the gaps.
- **C5** — negative provenance must be FINITE and VERSIONED. The store is
  constructed over a declared schema (a finite universe of variable names);
  :func:`negative_provenance` enumerates only schema variables and stamps the
  ledger length as its version — never a gesture at \"everything unobserved\".
  The action vocabulary (:class:`Action`) is likewise typed, because a tax
  practice cannot stop at \"suspended\".
- **C6** — the ledger proves that a flip was RECORDED at a given position under
  a given clock, not that anything became metaphysically true. That is exactly
  the claim an auditor needs, and this module makes no larger one — which is
  why the ledger is append-only with deliberately no removal or rewrite API.

Two-layer architecture (paper section 5): the ``working`` layer is cyclic (it
may be evicted back to LATENT — state cycles), while the ``cumulative`` layer
only ever climbs the knowledge order via :func:`k_join` (the ledger climbs).
Negative provenance is paper section 6, the proof-of-ignorance surface.

Pure standard library. British English throughout.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "EvidenceState", "not_", "and_", "or_", "k_join",
    "Claim", "base_claim", "c_and", "c_or", "c_not",
    "FlipRecord", "AssumptionRecord", "EvidenceStore",
    "Action", "Materiality", "default_materiality", "Decision",
    "decide", "file_under_assumption",
    "NegativeProvenance", "negative_provenance",
    "idr_leaf_writer",
]


class EvidenceState(Enum):
    """Belnap-Dunn four-valued evidence state as a (told_true, told_false) bit-pair.

    LATENT   — no evidence either way (Belnap's None).
    TRUE     — evidence for, none against.
    FALSE    — evidence against, none for.
    CONFLICT — evidence both for and against (Belnap's Both).
    """

    LATENT = (0, 0)
    TRUE = (1, 0)
    FALSE = (0, 1)
    CONFLICT = (1, 1)

    @property
    def told_true(self) -> int:
        """The told-true bit: 1 iff some evidence supports the variable."""
        return self.value[0]

    @property
    def told_false(self) -> int:
        """The told-false bit: 1 iff some evidence contradicts the variable."""
        return self.value[1]


def not_(x: EvidenceState) -> EvidenceState:
    """FDE negation: swap the bit-pair.

    LATENT and CONFLICT are fixed points (no evidence stays no evidence;
    contradictory evidence stays contradictory); TRUE and FALSE swap.
    """
    return EvidenceState((x.told_false, x.told_true))


def and_(a: EvidenceState, b: EvidenceState) -> EvidenceState:
    """FDE conjunction: (a.t AND b.t, a.f OR b.f)."""
    return EvidenceState((a.told_true & b.told_true, a.told_false | b.told_false))


def or_(a: EvidenceState, b: EvidenceState) -> EvidenceState:
    """FDE disjunction: (a.t OR b.t, a.f AND b.f)."""
    return EvidenceState((a.told_true | b.told_true, a.told_false & b.told_false))


def k_join(a: EvidenceState, b: EvidenceState) -> EvidenceState:
    """Knowledge-ordering join — evidence combination: (a.t OR b.t, a.f OR b.f).

    Idempotent and monotone; LATENT is the identity; TRUE joined with FALSE is
    CONFLICT (combining contradictory evidence records the contradiction rather
    than discarding either side).
    """
    return EvidenceState((a.told_true | b.told_true, a.told_false | b.told_false))


@dataclass(frozen=True)
class Claim:
    """A named claim, its evidence state, and the variables it rests on.

    ``assumptions`` is ATMS-style dependency tracking (de Kleer 1986): the set
    of base variable names whose evidence states this claim's state was derived
    from. Composite constructors UNION the sets, so a claim always knows which
    evidence would have to move for its state to change.
    """

    name: str
    state: EvidenceState
    assumptions: frozenset[str]


class IncoherentClaimError(ValueError):
    """A base claim asserts a state that contradicts the store's own evidence.

    AGORA #5019 round-2 concern 1: the gate must never bless a claim the
    evidence layer it cites refutes. Raised by :func:`decide` when a base
    claim about an in-schema variable carries a state different from that
    variable's working state — the coherent constructor is
    :meth:`EvidenceStore.claim`."""


def base_claim(name: str, state: EvidenceState) -> Claim:
    """A leaf claim about a single variable; it rests on exactly itself."""
    return Claim(name=name, state=state, assumptions=frozenset({name}))


def c_and(a: Claim, b: Claim) -> Claim:
    """Conjoin two claims: FDE conjunction of states, union of assumptions."""
    return Claim(f"({a.name} AND {b.name})", and_(a.state, b.state),
                 a.assumptions | b.assumptions)


def c_or(a: Claim, b: Claim) -> Claim:
    """Disjoin two claims: FDE disjunction of states, union of assumptions."""
    return Claim(f"({a.name} OR {b.name})", or_(a.state, b.state),
                 a.assumptions | b.assumptions)


def c_not(a: Claim) -> Claim:
    """Negate a claim: FDE negation of state; the dependency set is unchanged
    (the union over the single operand)."""
    return Claim(f"(NOT {a.name})", not_(a.state), a.assumptions)


@dataclass(frozen=True)
class FlipRecord:
    """One append-only ledger entry: the cumulative layer strictly climbed."""

    seq: int
    var: str
    prior: EvidenceState
    new: EvidenceState
    evidence_ref: str
    ts: str


@dataclass(frozen=True)
class AssumptionRecord:
    """One append-only assumption entry: a claim was filed under assumption.

    ``assumed`` is a sorted tuple of (variable, state name) pairs — hashable,
    ordered, and JSON-friendly. Ledger discipline is identical to
    :class:`FlipRecord`: appended, never mutated, never removed.
    """

    seq: int
    claim: str
    assumed: tuple[tuple[str, str], ...]
    marker: str
    ts: str


def _utc_now() -> str:
    """UTC timestamp in ISO-8601 form (aware, +00:00 suffix)."""
    return datetime.now(timezone.utc).isoformat()


class EvidenceStore:
    """The two-layer evidence architecture (paper section 5).

    ``working`` is the cyclic layer: it climbs under :meth:`observe` and may be
    evicted back to LATENT. ``cumulative`` is the monotone layer: it only ever
    climbs the knowledge order, and every strict climb appends a
    :class:`FlipRecord` to the ledger.

    The ledger is append-only with deliberately NO removal or rewrite API
    (council fold C6): its evidentiary value is precisely that it records when
    the SYSTEM came to hold each piece of evidence, in order, under a clock —
    an API that could un-know or reorder entries would destroy the only claim
    the ledger is entitled to make. Eviction therefore touches the working
    layer only; the ledger never un-knows.

    The universe of variables is the ``schema`` declared at construction —
    finite by construction (council fold C5), so negative provenance can
    enumerate what is unresolved rather than gesturing at everything
    unobserved.

    ``leaf_writer``, when injected, receives every appended record
    (:class:`FlipRecord` and :class:`AssumptionRecord`). It is OPTIONAL
    provenance over the MANDATORY in-memory ledger: a raising writer degrades
    to a logged warning and never loses the append (the fail-open shape of
    ``lib/sovereign_council.py::_seal_idr``).
    """

    def __init__(self, schema: Iterable[str],
                 leaf_writer: Callable[[object], object] | None = None) -> None:
        names = list(dict.fromkeys(schema))
        self.working: dict[str, EvidenceState] = {
            n: EvidenceState.LATENT for n in names}
        self.cumulative: dict[str, EvidenceState] = {
            n: EvidenceState.LATENT for n in names}
        self._ledger: list[FlipRecord] = []
        self._assumptions: list[AssumptionRecord] = []
        self._leaf_writer = leaf_writer

    @property
    def schema(self) -> tuple[str, ...]:
        """The declared, finite variable universe."""
        return tuple(self.working)

    @property
    def ledger(self) -> tuple[FlipRecord, ...]:
        """Read-only view of the append-only flip ledger."""
        return tuple(self._ledger)

    @property
    def assumptions(self) -> tuple[AssumptionRecord, ...]:
        """Read-only view of the append-only assumption ledger."""
        return tuple(self._assumptions)

    def _require_known(self, name: str) -> None:
        if name not in self.working:
            raise ValueError(
                f"unknown variable {name!r}: not in the declared schema "
                f"{self.schema!r} (the universe is finite by construction)")

    def _emit(self, record: object) -> None:
        """Hand *record* to the injected leaf writer, fail-open.

        A raising writer must never lose the (already performed) ledger
        append — catch, warn, continue, mirroring
        ``lib/sovereign_council.py::_seal_idr``.
        """
        if self._leaf_writer is None:
            return
        try:
            self._leaf_writer(record)
        except Exception as exc:  # noqa: BLE001 — leaf sealing is OPTIONAL
            # provenance over the MANDATORY in-memory ledger; a dead writer
            # degrades the record, never the store.
            logger.warning(
                "evidence leaf writer degraded (%s: %s); in-memory ledger retained",
                type(exc).__name__, exc)

    def observe(self, name: str, state: EvidenceState,
                evidence_ref: str) -> FlipRecord | None:
        """Combine new evidence for *name*; record iff cumulative strictly climbs.

        Both layers absorb the evidence via :func:`k_join`. Re-observing
        evidence the cumulative layer already holds is a no-op returning None
        (idempotent: f applied twice equals f once). Unknown names raise
        ValueError.
        """
        self._require_known(name)
        prior = self.cumulative[name]
        new = k_join(prior, state)
        self.working[name] = k_join(self.working[name], state)
        if new is prior:
            return None
        self.cumulative[name] = new
        record = FlipRecord(seq=len(self._ledger), var=name, prior=prior,
                            new=new, evidence_ref=evidence_ref, ts=_utc_now())
        self._ledger.append(record)
        self._emit(record)
        return record

    def evict(self, name: str) -> None:
        """Forget *name* in the WORKING layer only (state cycles).

        The cumulative layer and the ledger are untouched — the ledger never
        un-knows. Re-observing the same evidence afterwards re-populates the
        working layer without a new ledger record (cumulative already holds
        it); contrary evidence afterwards climbs cumulative to CONFLICT and is
        recorded.
        """
        self._require_known(name)
        self.working[name] = EvidenceState.LATENT

    def claim(self, name: str) -> Claim:
        """The canonical evidence-backed base claim for *name*.

        Its state IS the working state, so a claim built here can never lie
        about the evidence it rests on (the coherent path concern 1 of the
        AGORA #5019 round-2 review demands). Unknown names raise — the
        universe is the declared schema.
        """
        if name not in self.working:
            raise ValueError(f"unknown variable {name!r}: not in the declared schema")
        return Claim(name, self.working[name], frozenset({name}))

    def record_assumption(self, claim_name: str,
                          assumed: dict[str, EvidenceState],
                          marker: str) -> AssumptionRecord:
        """Append an assumption event; ledger discipline identical to flips."""
        for var in assumed:
            self._require_known(var)
        entry = AssumptionRecord(
            seq=len(self._assumptions),
            claim=claim_name,
            assumed=tuple(sorted((v, s.name) for v, s in assumed.items())),
            marker=marker,
            ts=_utc_now(),
        )
        self._assumptions.append(entry)
        self._emit(entry)
        return entry


class Action(Enum):
    """The typed action vocabulary (council fold C5) — a practice cannot stop
    at \"suspended\"; the professional response to a gap is an action."""

    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    BOUNDED_SCENARIOS = "BOUNDED_SCENARIOS"
    FILE_UNDER_ASSUMPTION = "FILE_UNDER_ASSUMPTION"
    ESCALATE = "ESCALATE"
    SUSPEND = "SUSPEND"


Materiality = Callable[[str], bool]


def default_materiality(_var: str) -> bool:
    """Every variable is material by default — the conservative floor.

    An unmapped variable must fail TOWARD blocking: presuming a gap immaterial
    without a domain rule would let unresolved evidence ride silently into a
    DEFINITIVE verdict, which is the exact hallucination shape the gate exists
    to stop. Council fold C1 licenses materiality-sensitive suspension, not
    materiality-by-omission — so the floor is \"material until a domain policy
    says otherwise\".
    """
    return True


@dataclass(frozen=True)
class Decision:
    """The gate's output: a verdict plus the annotated evidence gaps.

    INVARIANT (AGORA #5019 rounds 1+2, fixed): ``verdict == "DEFINITIVE"``
    implies ``material_unresolved == ()`` AND ``claim.state`` is never
    CONFLICT. :func:`decide` is stricter still (classical states only);
    :func:`file_under_assumption` may mint DEFINITIVE for a LATENT claim —
    the move exists precisely for a not-yet-derivable conclusion — but only
    with every material gap covered by the assumed set, an
    ``assumption_marker`` recording the move, and never over a CONFLICT
    (a dispute cannot be papered over by an assumption).
    """

    verdict: str
    claim: Claim
    material_unresolved: tuple[str, ...]
    immaterial_unresolved: tuple[str, ...]
    recommended: Action | None
    assumption_marker: str | None = None


_UNRESOLVED = (EvidenceState.LATENT, EvidenceState.CONFLICT)


def _working_state(store: EvidenceStore, var: str) -> EvidenceState:
    """Working state of *var*; a variable outside the store is LATENT
    (never observed — the conservative reading)."""
    return store.working.get(var, EvidenceState.LATENT)


def _split_unresolved(variables: Iterable[str], store: EvidenceStore,
                      materiality: Materiality) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Partition unresolved variables (working LATENT/CONFLICT) by materiality."""
    material: list[str] = []
    immaterial: list[str] = []
    for var in sorted(variables):
        if _working_state(store, var) in _UNRESOLVED:
            (material if materiality(var) else immaterial).append(var)
    return tuple(material), tuple(immaterial)


def _recommend(claim_state: EvidenceState, material: tuple[str, ...],
               store: EvidenceStore, sources: dict[str, str]) -> Action:
    """Policy ladder for a SUSPENDED verdict (council fold C1 — policy, not logic).

    A claim whose OWN state is CONFLICT escalates (the dispute is in the
    conclusion itself, not merely an input — AGORA #5019 round-2 concern 2);
    CONFLICT among material variables escalates likewise; a material LATENT
    variable with a known source is a concrete evidence request; only when
    none applies is bare SUSPEND the last resort. BOUNDED_SCENARIOS and
    FILE_UNDER_ASSUMPTION are selected by callers with domain knowledge,
    never auto-recommended.
    """
    if claim_state is EvidenceState.CONFLICT:
        return Action.ESCALATE
    if any(_working_state(store, v) is EvidenceState.CONFLICT for v in material):
        return Action.ESCALATE
    if any(v in sources for v in material):
        return Action.REQUEST_EVIDENCE
    return Action.SUSPEND


def _check_base_coherence(claim: Claim, store: EvidenceStore) -> None:
    """Refuse a base claim whose asserted state contradicts the store.

    Applies to BASE claims about in-schema variables (assumptions == {name}):
    their state must equal the variable's working state — exactly what
    :meth:`EvidenceStore.claim` constructs. Compound claims are coherent BY
    CONSTRUCTION when their leaves come from ``store.claim()`` and are
    combined via :func:`c_and`/:func:`c_or`/:func:`c_not` (the FDE tables
    preserve derivation); a hand-built compound with an arbitrary state
    cannot be recomputed without an expression tree and is out of contract.
    """
    if claim.assumptions == frozenset({claim.name}) and claim.name in store.working:
        actual = store.working[claim.name]
        if claim.state is not actual:
            raise IncoherentClaimError(
                f"claim {claim.name!r} asserts {claim.state.name} but the "
                f"store's evidence holds {actual.name} — build base claims "
                "with store.claim() so they cannot contradict the evidence")


def decide(claim: Claim, store: EvidenceStore,
           materiality: Materiality = default_materiality,
           sources: dict[str, str] | None = None) -> Decision:
    """Apply the suspension POLICY (fold C1) to *claim* over *store*.

    DEFINITIVE iff the claim's state is classical (TRUE or FALSE) AND no
    MATERIAL assumption is unresolved in the working layer. Immaterial gaps
    ride along as annotation — they never block. Otherwise SUSPENDED, with the
    recommended action from :func:`_recommend`. A base claim contradicting
    the store's own evidence raises :class:`IncoherentClaimError` before any
    verdict — the gate compares the claim against the evidence, it does not
    merely count gaps (AGORA #5019 round-2 concern 1).

    A NON-SCHEMA assumption reads as LATENT here (conservative: it suspends
    under the default materiality and can only be unblocked by an explicit
    policy), while :func:`negative_provenance` excludes it from its finite
    universe — deliberate asymmetry: deciding fails toward blocking, the
    provenance block never names variables outside the declared schema.
    """
    _check_base_coherence(claim, store)
    material, immaterial = _split_unresolved(claim.assumptions, store, materiality)
    if claim.state in (EvidenceState.TRUE, EvidenceState.FALSE) and not material:
        return Decision("DEFINITIVE", claim, material, immaterial, None)
    return Decision("SUSPENDED", claim, material, immaterial,
                    _recommend(claim.state, material, store, sources or {}))


def file_under_assumption(claim: Claim, store: EvidenceStore,
                          assumed: dict[str, EvidenceState],
                          marker: str,
                          materiality: Materiality = default_materiality) -> Decision:
    """The explicit \"file now, amend later\" move.

    Records the assumption event on the store's append-only assumption ledger
    (and through its leaf_writer channel), then returns a DEFINITIVE Decision
    carrying *marker*. Variables in *assumed* are treated as resolved for the
    annotation fields; any OTHER unresolved assumptions still ride along as
    annotation under the marker — the verdict is definitive BY THE CALLER'S
    EXPLICIT MOVE, which is why ``recommended`` names FILE_UNDER_ASSUMPTION.
    *materiality* partitions the remaining gaps exactly as :func:`decide`
    would (interface parity — a domain caller sees one policy on both).

    The assumed set must COVER every material gap: filing "under assumption"
    while a material variable stays unassumed would mint a DEFINITIVE
    decision that contradicts itself (the AGORA #5019 blocking concern).
    Such a call raises ValueError BEFORE any side effect — no assumption
    record is written for a refused filing.
    """
    if claim.state is EvidenceState.CONFLICT:
        raise ValueError(
            f"file_under_assumption: claim {claim.name!r} has state CONFLICT "
            "— contradictory evidence is a dispute, not a gap; it cannot be "
            "papered over by an assumption. Escalate or resolve the conflict "
            "first (Decision invariant, AGORA #5019 round 2). A LATENT claim "
            "IS fileable: filing under assumption exists precisely for a "
            "conclusion not yet derivable, provided the assumed set covers "
            "every material gap."
        )
    _check_base_coherence(claim, store)
    remaining = claim.assumptions - set(assumed)
    material, immaterial = _split_unresolved(remaining, store, materiality)
    if material:
        raise ValueError(
            f"file_under_assumption: material gaps {material} are not covered "
            "by the assumed set — assume them explicitly or declare them "
            "immaterial. A DEFINITIVE decision may never carry a material "
            "unresolved variable (Decision invariant)."
        )
    store.record_assumption(claim.name, assumed, marker)
    return Decision("DEFINITIVE", claim, (), immaterial,
                    Action.FILE_UNDER_ASSUMPTION, assumption_marker=marker)


@dataclass(frozen=True)
class NegativeProvenance:
    """The proof-of-ignorance surface (paper section 6).

    Versioned by construction: ``version`` is the flip-ledger length and
    ``assumption_version`` the assumption-ledger length at issue. Because the
    WORKING layer can change with no ledger movement (evict + re-observe of
    held evidence), ``content_digest`` — sha256 over the canonical content —
    pins what this block actually said, so two blocks can never silently
    coincide (AGORA #5019 round-2 concern 3). ``ledger_conflicts`` discloses
    every claim variable whose CUMULATIVE state is CONFLICT even when the
    working layer currently looks classical: the audit surface must never
    hide a conflict its own ledger proves. Only schema variables ever appear
    — finite by construction (council fold C5).
    """

    version: int
    assumption_version: int
    manifest_basis: dict[str, tuple[str, str]]
    latent_at_issue: tuple[str, ...]
    conflict_at_issue: tuple[str, ...]
    ledger_conflicts: tuple[str, ...]
    conditions: str
    content_digest: str

    def to_dict(self) -> dict[str, object]:
        """Plain-JSON-shaped mapping (tuples become lists)."""
        return {
            "version": self.version,
            "assumption_version": self.assumption_version,
            "manifest_basis": {k: list(v) for k, v in self.manifest_basis.items()},
            "latent_at_issue": list(self.latent_at_issue),
            "conflict_at_issue": list(self.conflict_at_issue),
            "ledger_conflicts": list(self.ledger_conflicts),
            "conditions": self.conditions,
            "content_digest": self.content_digest,
        }


def _provenance_digest(content: dict[str, object]) -> str:
    """sha256 over the canonical JSON of a provenance block's content."""
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _manifest_basis(store: EvidenceStore,
                    claim: Claim) -> tuple[list[str], dict[str, tuple[str, str]]]:
    """Schema variables the claim rests on, plus their manifest basis.

    The basis maps each in-schema variable to its current working state name
    and the most recent evidence_ref the ledger holds for it (empty string
    when never observed). Non-schema assumption names are excluded — the
    universe is finite by construction (council fold C5).
    """
    in_schema = sorted(v for v in claim.assumptions if v in store.working)
    latest_ref = {rec.var: rec.evidence_ref for rec in store.ledger}
    basis = {v: (store.working[v].name, latest_ref.get(v, "")) for v in in_schema}
    return in_schema, basis


def _split_material_gaps(variables: Iterable[str], store: EvidenceStore,
                         materiality: Materiality) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Partition the MATERIAL variables by working gap kind (LATENT vs CONFLICT)."""
    latent: list[str] = []
    conflict: list[str] = []
    for var in variables:
        if not materiality(var):
            continue
        state = _working_state(store, var)
        if state is EvidenceState.LATENT:
            latent.append(var)
        elif state is EvidenceState.CONFLICT:
            conflict.append(var)
    return tuple(latent), tuple(conflict)


def negative_provenance(store: EvidenceStore, claim: Claim,
                        materiality: Materiality = default_materiality) -> NegativeProvenance:
    """Build the finite, versioned negative-provenance block for *claim*.

    ``manifest_basis`` maps each SCHEMA variable the claim rests on to its
    current WORKING state name and the most recent evidence_ref the ledger
    holds for it (empty string when never observed). The at-issue lists name
    the MATERIAL unresolved variables in the working layer;
    ``ledger_conflicts`` additionally names every claim variable whose
    CUMULATIVE state is CONFLICT — disclosed unconditionally, because the
    block must never assert finality over a conflict its own ledger records
    (AGORA #5019 round-2 concern 3). The conditions string names both. The
    ``content_digest`` pins the emitted content beyond the ledger version.
    Non-schema assumption names are excluded — the universe is finite by
    construction.
    """
    in_schema, basis = _manifest_basis(store, claim)
    latent, conflict = _split_material_gaps(in_schema, store, materiality)
    ledger_conflicts = tuple(
        v for v in in_schema if store.cumulative[v] is EvidenceState.CONFLICT)
    if latent:
        conditions = (f"claim {claim.name} is final iff "
                      f"{', '.join(latent)} resolve")
    else:
        conditions = f"claim {claim.name} is final on the recorded manifest basis"
    if ledger_conflicts:
        conditions += (
            f"; NOTE: the flip ledger records conflicting evidence on "
            f"{', '.join(ledger_conflicts)} — see ledger before relying on this block")
    content = {
        "version": len(store.ledger),
        "assumption_version": len(store.assumptions),
        "manifest_basis": {k: list(v) for k, v in basis.items()},
        "latent_at_issue": list(latent),
        "conflict_at_issue": list(conflict),
        "ledger_conflicts": list(ledger_conflicts),
        "conditions": conditions,
    }
    return NegativeProvenance(
        version=len(store.ledger),
        assumption_version=len(store.assumptions),
        manifest_basis=basis,
        latent_at_issue=latent,
        conflict_at_issue=conflict,
        ledger_conflicts=ledger_conflicts,
        conditions=conditions,
        content_digest=_provenance_digest(content),
    )


def _idr_payload(record: object) -> tuple[str, str, dict[str, object]]:
    """(kind, prompt, decision) for an IDR leaf over a ledger record."""
    if isinstance(record, FlipRecord):
        return (
            "evidence-flip",
            f"evidence-flip {record.var}: {record.prior.name}->{record.new.name}",
            {"var": record.var, "prior": record.prior.name,
             "new": record.new.name, "evidence_ref": record.evidence_ref,
             "seq": record.seq},
        )
    assumption: AssumptionRecord = record  # type: ignore[assignment]
    return (
        "evidence-assumption",
        f"evidence-assumption {assumption.claim}: {assumption.marker}",
        {"claim": assumption.claim,
         "assumed": [list(pair) for pair in assumption.assumed],
         "marker": assumption.marker, "seq": assumption.seq},
    )


_PRECOG_CACHE: tuple | None = None


def _load_precog() -> tuple | None:
    """One-time cached import of the PRECOG IDR primitives.

    The sys.path insert and import happen at most once per process (AGORA
    #5019 round-2 concern 4: no per-record interpreter-state mutation inside
    a provenance callback). Returns (build_idr, append_idr) or None; a failed
    import is NOT cached, so a later repaired environment can still seal.
    """
    global _PRECOG_CACHE
    if _PRECOG_CACHE is not None:
        return _PRECOG_CACHE
    try:
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from lib.precog.idr import append_idr, build_idr
        _PRECOG_CACHE = (build_idr, append_idr)
        return _PRECOG_CACHE
    except Exception as exc:  # noqa: BLE001 — fail-open, see idr_leaf_writer
        logger.warning(
            "IDR signing degraded (%s: %s); in-memory ledger is the record",
            type(exc).__name__, exc)
        return None


def idr_leaf_writer(
        path: Path | None = None,
) -> Callable[[FlipRecord | AssumptionRecord], str | None]:
    """A leaf_writer that seals each record as a signed, CHAINED PRECOG IDR leaf.

    Each successful leaf names its predecessor (``predecessor_idr`` = the
    previous leaf's id, depth incremented), so the exported chain carries
    hash-signed linkage rather than relying on file-append order (AGORA #5019
    round-2 concern 4). A failed write does not advance the chain head.
    Returns the leaf id, or None on any failure: the ENTIRE body is fail-open
    — IDR is OPTIONAL provenance over the MANDATORY in-memory ledger,
    mirroring ``lib/sovereign_council.py::_seal_idr``.
    """
    head: dict[str, object] = {"id": None, "depth": -1}

    def _write(record: FlipRecord | AssumptionRecord) -> str | None:
        loaded = _load_precog()
        if loaded is None:
            return None
        build_idr, append_idr = loaded
        try:
            kind, prompt, decision = _idr_payload(record)
            idr = build_idr(prompt=prompt, fingerprint="evidence-state",
                            decision=decision,
                            predecessor_idr=head["id"],
                            depth=int(head["depth"]) + 1,
                            kind=kind)
            append_idr(idr, path=path)
            head["id"] = idr.id
            head["depth"] = int(head["depth"]) + 1
            return idr.id
        except Exception as exc:  # noqa: BLE001 — IDR is OPTIONAL provenance
            # over the MANDATORY in-memory ledger; a dead IDR path degrades the
            # seal, never the store.
            logger.warning(
                "IDR signing degraded (%s: %s); in-memory ledger is the record",
                type(exc).__name__, exc)
            return None
    return _write
