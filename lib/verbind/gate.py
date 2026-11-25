"""Verbind gate -- the state machine of docs/VERBIND.md section 3, kept pure.

Every effect reaches this module as an injected callable on ``GateDeps``. It
holds no I/O, reads no environment and imports nothing from any harness: an
adapter builds the ``Envelope`` and carries out what ``GateResult`` asks for,
and this module only decides. It mirrors ``formal/VERBIND.lean`` (the source of
record is the contract branch; ``tests/test_verbind_lean_bridge.py`` checks the
two agree), and the clauses ``tests/test_verbind_gate.py`` pins are these:

  1. no infinite loop           every loop here is bounded
  2. a crash never creates      an exception in any decision callable yields
                                HITL and no binding (``_guarded``); the
                                authority floor's own crash is a refusal
                                instead (clause 9)
  3. HITL bounded at 11         ``KOPPEL_MAX_ROUNDS``, counted across separate
                                invocations through ``rounds_used``
  4. never ask twice            a deferred verb and a bound verb never reach
                                ``koppel``
  5. a bound verb is O(1)       it makes zero calls to the meaning floor, the
                                council or ``koppel``, and gives the same
                                binding every time
  6. the council only tightens  ``tighten`` can withdraw a recommendation,
                                never add one, reorder candidates or relax
  7. harness independence       nothing here knows which harness is calling
  8. every path seals its own   the sealed ``Outcome`` is the one the path
     outcome                    actually took, never a default
  9. authority on every use     a binding fixes MEANING, never AUTHORITY: the
                                floor is re-checked before every APPLY, a
                                refusal applies nothing and counts no use, and
                                a floor that crashes has refused (``_authority``)

and two properties of the Lean model the Koppel path keeps: it yields RATIFIED
only after a refined answer, and it never yields CREATE
(``koppel_ratified_only_after_refined``, ``koppel_never_creates``).

SEAL and SURFACE run in a ``finally`` block, after the decision, so a failure
inside either is reported in ``reason`` and never changes the verdict. SURFACE
runs on every path. SEAL runs on every path past L0: the contract's two L0
returns -- no unknown verb, and a verb deferred before -- cost nothing and seal
nothing (``L0_OUTCOMES``), so a deferred verb typed again adds nothing to the
receipt chain or the IDR chain. What SEAL records is the ``Outcome`` the path actually
took -- BOUND, RATIFIED, DEFERRED, DENIED or EXHAUSTED in the contract's
terms -- never a default: ``GateResult.outcome`` has no default value, so a
path that forgot to name its outcome could not even be constructed.

Four verdicts, as in the Lean model. RATIFIED is a binding the operator
approved in a Koppel round. CREATE is an autonomous binding, and no v1 path
produces it: ``_checked`` refuses one at the boundary, so even a future bug
that returned CREATE would surface as a crash, which yields HITL. A prompt
from an untrusted origin is never asked anything, and nor is one whose floor
crashed: whatever it answered could not be bound, and recording its deferral
would silence the question for the operator. The contract would still run the
Koppel round there and deny the refined answer; this asks nothing it could not
honour, which is the same denial reached sooner.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Optional

__all__ = [
    "ANSWER_KINDS",
    "Action",
    "Outcome",
    "Authority",
    "Binding",
    "Candidate",
    "Envelope",
    "Evidence",
    "GateDeps",
    "GateResult",
    "KOPPEL_MAX_ROUNDS",
    "KoppelAnswer",
    "KoppelRequest",
    "L0_OUTCOMES",
    "Meaning",
    "NO_EVIDENCE",
    "ORIGIN_OPERATOR",
    "PROMOTE_AT",
    "REFUSED",
    "SAFE_RANK",
    "V1_FORBIDDEN",
    "Verdict",
    "council",
    "describe_operation",
    "digest",
    "gate",
    "gate_answer",
    "promotes",
    "render_surface",
    "safer_or_eq",
    "tighten",
]


# ── verdicts: the Lean model, transcribed ────────────────────────────────────


class Verdict(str, Enum):
    """The gate's answer, ranked by how much a human stands behind the act."""

    PASS = "pass"
    HITL = "hitl"
    RATIFIED = "ratified"  # bound because the operator approved it in a Koppel round
    CREATE = "create"      # an autonomous binding: no v1 path may produce it


#: Identical to ``Verdict.safeRank`` in formal/VERBIND.lean. Higher is safer.
SAFE_RANK = {Verdict.CREATE: 0, Verdict.RATIFIED: 1, Verdict.HITL: 2, Verdict.PASS: 3}

#: Verdicts the v1 gate refuses to emit; ``_checked`` turns one into a crash.
V1_FORBIDDEN = frozenset({Verdict.CREATE})


def safer_or_eq(a: Verdict, b: Verdict) -> bool:
    """``a`` is at least as safe as ``b`` (Lean ``SaferOrEq``)."""
    return SAFE_RANK[b] <= SAFE_RANK[a]


def council(floor: Verdict, veto: bool) -> Verdict:
    """Lean ``council``: a veto may move CREATE to HITL; nothing else moves."""
    if floor is Verdict.CREATE and veto:
        return Verdict.HITL
    return floor


class Action(str, Enum):
    """What the adapter should do with the result."""

    NONE = "none"    # nothing; the prompt runs as it would have
    APPLY = "apply"  # run the bound operation in ``GateResult.application``
    ASK = "ask"      # run the Koppel round in ``GateResult.koppel_payload``
    DEFER = "defer"  # the operator deferred the verb; treat the prompt as text
    DENY = "deny"    # the authority floor refused; treat the prompt as text


class Outcome(str, Enum):
    """What this prompt ACTUALLY did -- assigned on every path, sealed as is.

    The contract's five (docs/VERBIND.md section 3): every return path assigns
    its own outcome before returning, and FINALLY seals it, never a default.
    ``GateResult.outcome`` has no default value, so a path cannot forget one.
    Not the Lean model's ``Outcome``, which is an inference that returned or
    crashed; the two share a word, not a meaning.
    """

    BOUND = "bound"            # a cached binding was applied
    RATIFIED = "ratified"      # the operator refined; a binding was created and applied
    DEFERRED = "deferred"      # the operator deferred the verb
    DENIED = "denied"          # the authority floor refused
    EXHAUSTED = "exhausted"    # eleven rounds with no usable answer
    # This implementation's other paths, each named rather than defaulted:
    ASKED = "asked"            # a Koppel round handed to the adapter, awaiting an answer
    ALREADY_DEFERRED = "already-deferred"  # deferred earlier; not asked again
    NO_VERB = "no-verb"        # nothing for Verbind; nothing is sealed
    UNANSWERED = "unanswered"  # an answer that was neither refined nor deferred
    CRASHED = "crashed"        # a decision callable raised; HITL, nothing created


#: The contract's two L0 returns. They cost nothing and seal nothing
#: (docs/VERBIND.md section 3, FINALLY); every path past them seals what it did.
L0_OUTCOMES = frozenset({Outcome.NO_VERB, Outcome.ALREADY_DEFERRED})

KOPPEL_MAX_ROUNDS = 11
ORIGIN_OPERATOR = "operator"
ANSWER_KINDS = ("refined", "deferred", "unclear", "pending")


def digest(*parts: str) -> str:
    """sha256 over length-prefixed parts, so no two part lists collide."""
    h = hashlib.sha256()
    for part in parts:
        data = str(part).encode("utf-8")
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


# ── data the gate passes around ──────────────────────────────────────────────


@dataclass(frozen=True)
class Envelope:
    """The adapter's trusted record of one invocation. Only adapters build it.

    ``origin`` is the one field the gate trusts: ``operator`` means the
    adapter established that the operator typed this prompt in their own
    session. Every other value, including a malformed one, is untrusted.
    """

    origin: str
    harness: str = ""
    session: str = ""
    ref: str = ""

    @property
    def trusted(self) -> bool:
        return self.origin == ORIGIN_OPERATOR

    def to_dict(self) -> dict:
        return {"origin": self.origin, "harness": self.harness,
                "session": self.session, "ref": self.ref}

    @classmethod
    def from_dict(cls, data: Any) -> "Envelope":
        """Tolerant parse: anything malformed becomes an untrusted envelope."""
        if not isinstance(data, dict):
            return cls(origin="unknown")
        return cls(
            origin=str(data.get("origin") or "unknown"),
            harness=str(data.get("harness") or ""),
            session=str(data.get("session") or ""),
            ref=str(data.get("ref") or ""),
        )


@dataclass(frozen=True)
class Meaning:
    """What a verb means: one existing operation, plus the operator's words."""

    kind: str
    target: str
    note: str = ""

    def digest(self) -> str:
        return digest("verbind/meaning", self.kind, self.target, self.note)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "target": self.target, "note": self.note}

    @classmethod
    def from_dict(cls, data: dict) -> "Meaning":
        return cls(str(data["kind"]), str(data["target"]), str(data.get("note") or ""))


@dataclass(frozen=True)
class Candidate:
    """One catalogue entry the meaning floor ranked."""

    kind: str
    slug: str
    score: float
    summary: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "slug": self.slug,
                "score": round(self.score, 6), "summary": self.summary}


@dataclass(frozen=True)
class Evidence:
    """The meaning floor's evidence. It carries no verdict, by construction.

    ``margin`` is how far the top candidate stands clear of the runner-up, on
    a 0 to 1 scale; ``uncertain`` withdraws the recommendation; ``gaps`` say
    what was unclear, in words a Koppel question can carry.
    """

    candidates: tuple = ()
    margin: float = 0.0
    uncertain: bool = True
    gaps: tuple = ()

    @property
    def top(self) -> Optional[Candidate]:
        return self.candidates[0] if self.candidates else None

    @property
    def recommended(self) -> Optional[Candidate]:
        return None if self.uncertain else self.top

    def to_dict(self) -> dict:
        return {"candidates": [c.to_dict() for c in self.candidates],
                "margin": round(self.margin, 6), "uncertain": self.uncertain,
                "gaps": list(self.gaps)}


NO_EVIDENCE = Evidence(gaps=("no-evidence",))


def tighten(evidence: Evidence, opinion: Any) -> Evidence:
    """The pre-council's only power: make the evidence MORE cautious.

    It reads two booleans, ``veto`` and ``uncertain``, and either can only mark
    the evidence uncertain, which withdraws the recommendation. It cannot add,
    drop or reorder candidates, and it cannot make uncertain evidence certain.
    Anything else the council returns is ignored.
    """
    if opinion is None:
        return evidence
    veto = bool(getattr(opinion, "veto", False))
    unsure = bool(getattr(opinion, "uncertain", False))
    if not (veto or unsure):
        return evidence
    gap = "council-veto" if veto else "council-uncertain"
    return replace(evidence, uncertain=True, gaps=evidence.gaps + (gap,))


@dataclass(frozen=True)
class Authority:
    """The deterministic authority floor's answer for one envelope.

    ``allowed`` holds the ``(kind, slug)`` operations a binding may target
    under this envelope; in v1 that is the reviewed read-only list.
    """

    ok: bool
    reason: str = ""
    allowed: frozenset = frozenset()

    def permits(self, meaning: Any) -> bool:
        if not self.ok or not isinstance(meaning, Meaning):
            return False
        return (meaning.kind, meaning.target) in self.allowed


#: The contract's starting authority (docs/VERBIND.md section 3: ``authority =
#: REFUSED``). Nothing is authorised until the floor answers, so a floor that
#: crashes, or answers with anything but an ``Authority``, has refused.
REFUSED = Authority(False, "refused")


@dataclass(frozen=True)
class KoppelRequest:
    verb: str
    args: str
    evidence: Evidence
    round: int


@dataclass(frozen=True)
class KoppelAnswer:
    """``refined`` carries a Meaning; ``pending`` hands the round to the adapter."""

    kind: str
    meaning: Optional[Meaning] = None
    payload: Optional[dict] = None


@dataclass(frozen=True)
class Binding:
    """A verbinding: the verb, what it means, and how often it was used.

    ``version`` counts corrections within one lineage (a forget starts a new
    lineage at 1). ``fixture`` is what QUALITY replays.
    """

    verb: str
    meaning: Meaning
    version: int = 1
    uses: int = 1
    created: str = ""
    ultralearn_seal: str = ""
    fixture: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"verb": self.verb, "meaning": self.meaning.to_dict(),
                "version": self.version, "uses": self.uses,
                "created": self.created, "ultralearn_seal": self.ultralearn_seal}


@dataclass(frozen=True)
class GateResult:
    verdict: Verdict
    outcome: Outcome  # no default: every path names what it did
    action: Action = Action.NONE
    verb: Optional[str] = None
    args: str = ""
    reason: str = ""
    evidence: Optional[Evidence] = None
    koppel_payload: Optional[dict] = None
    binding: Optional[Binding] = None
    application: Optional[dict] = None
    independent: Optional[int] = None
    round: Optional[int] = None
    receipt_id: Optional[str] = None
    surface: str = ""

    @property
    def recommended(self) -> Optional[Candidate]:
        return self.evidence.recommended if self.evidence else None

    def to_dict(self) -> dict:
        rec = self.recommended
        return {
            "verdict": self.verdict.value,
            "outcome": self.outcome.value,
            "action": self.action.value,
            "verb": self.verb,
            "args": self.args,
            "reason": self.reason,
            "recommended": rec.to_dict() if rec else None,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "koppel_payload": self.koppel_payload,
            "binding": self.binding.to_dict() if self.binding else None,
            "application": self.application,
            "independent": self.independent,
            "round": self.round,
            "receipt_id": self.receipt_id,
            "surface": self.surface,
        }


@dataclass
class GateDeps:
    """Every effect the gate can have, injected. Names follow the contract."""

    leading_unknown_verb: Callable[[str], tuple]          # prompt -> (verb|None, args)
    unresolved: Callable[[str], bool]                     # UNRESOLVED(verb)
    lookup: Callable[[str], Optional[Binding]]            # LOOKUP(verb)
    independent: Callable[[str, Binding, Envelope], int]  # INDEPENDENT(prompt, b)
    record_use: Callable[[Binding, int], Binding]         # b.uses += n
    quality: Callable[[Binding], bool]                    # QUALITY(b)
    promote: Callable[[Binding], Any]                     # PROMOTE(b)
    capability_floor: Callable[[Envelope], Authority]     # CAPABILITY_FLOOR
    floor_evidence: Callable[[str, str, str], Evidence]   # FLOOR_EVIDENCE
    koppel: Callable[[KoppelRequest], KoppelAnswer]       # KOPPEL
    verbind: Callable[[str, Meaning, Envelope], Binding]  # VERBIND: build, no write
    commit: Callable[[Binding], Binding]                  # persist a binding
    apply: Callable[[Binding, Envelope, str], dict]       # APPLY: what to run
    record_unresolved: Callable[[str, str], Any]
    record_denied: Callable[[str, Optional[Meaning], str], Any]
    rounds_used: Callable[[str], int]
    record_round: Callable[[str], Any]
    seal: Callable[[GateResult, Envelope, str], Optional[str]]
    surface: Callable[[GateResult], str]
    pre_council: Optional[Callable[[Evidence], Any]] = None


# ── the gate ─────────────────────────────────────────────────────────────────


@dataclass
class _Ctx:
    """What the gate learned so far, so a crash result can still name the verb."""

    verb: Optional[str] = None
    args: str = ""
    evidence: Optional[Evidence] = None


def gate(prompt: str, envelope: Envelope, deps: GateDeps) -> GateResult:
    """Decide one prompt (docs/VERBIND.md section 3)."""
    return _guarded(lambda ctx: _decide(prompt, envelope, deps, ctx), prompt, envelope, deps)


def gate_answer(verb: str, answer: KoppelAnswer, envelope: Envelope,
                deps: GateDeps, args: str = "") -> GateResult:
    """Settle a Koppel round an adapter ran on its own (the asynchronous path).

    A harness that cannot await the operator inside one call gets HITL with a
    payload from ``gate``, asks, and reports the answer here. The same authority
    floor, the same commit-last ordering and the same SEAL/SURFACE apply.
    """
    return _guarded(lambda ctx: _answer(verb, args, answer, envelope, deps, ctx),
                    "", envelope, deps)


def _guarded(decide: Callable[[_Ctx], GateResult], prompt: str,
             envelope: Envelope, deps: GateDeps) -> GateResult:
    """Clause 2 and the FINALLY: any crash yields HITL; every path is sealed
    with the outcome it actually took (clause 8)."""
    ctx = _Ctx()
    result = GateResult(Verdict.HITL, Outcome.CRASHED, reason="standing")
    try:
        result = _checked(decide(ctx))
    except Exception as exc:  # noqa: BLE001 -- clause 2 covers every callable
        result = GateResult(Verdict.HITL, Outcome.CRASHED, Action.NONE, ctx.verb, ctx.args,
                            reason=f"crash:{type(exc).__name__}", evidence=ctx.evidence)
    finally:
        result = _seal_and_surface(result, prompt, envelope, deps)
    return result


def _checked(result: Any) -> GateResult:
    """Refuse a malformed decision, so a broken callable cannot smuggle one out.

    The v1 boundary lives here too: a CREATE verdict -- an autonomous binding
    -- is refused outright, whatever produced it, and so becomes a crash.
    """
    if not isinstance(result, GateResult):
        raise TypeError("the gate's decision must be a GateResult")
    if result.verdict in V1_FORBIDDEN:
        raise ValueError(f"v1 never emits {result.verdict.value}: no autonomous binding")
    if result.verdict is Verdict.RATIFIED and result.binding is None:
        raise ValueError("RATIFIED without a binding")
    return result


def _seal_and_surface(result: GateResult, prompt: str, envelope: Envelope,
                      deps: GateDeps) -> GateResult:
    """FINALLY: SEAL past L0 (clause 8), SURFACE on every path."""
    if result.outcome not in L0_OUTCOMES:
        try:
            result = replace(result, receipt_id=deps.seal(result, envelope, prompt))
        except Exception as exc:  # noqa: BLE001 -- a failed seal never changes the decision
            result = replace(result, reason=f"{result.reason};seal-failed:{type(exc).__name__}")
    try:
        result = replace(result, surface=str(deps.surface(result) or ""))
    except Exception:  # noqa: BLE001
        result = replace(result, surface="")
    return result


def _decide(prompt: str, envelope: Envelope, deps: GateDeps, ctx: _Ctx) -> GateResult:
    verb, args = deps.leading_unknown_verb(prompt)
    ctx.verb, ctx.args = verb, args or ""
    if verb is None:
        return GateResult(Verdict.PASS, Outcome.NO_VERB, reason="no-unknown-verb")
    if deps.unresolved(verb):  # never ask the same thing twice
        return GateResult(Verdict.PASS, Outcome.ALREADY_DEFERRED, Action.NONE, verb, ctx.args,
                          reason="deferred-before")
    bound = deps.lookup(verb)
    if bound is not None:
        return _apply_bound(prompt, envelope, deps, ctx, bound)
    return _fresh(prompt, envelope, deps, ctx)


def _authority(envelope: Envelope, deps: GateDeps) -> Authority:
    """CAPABILITY_FLOOR, failing closed (clause 9).

    The contract starts from ``authority = REFUSED``. A floor that raises, or
    answers with anything but an ``Authority``, leaves that refusal in force:
    nothing is authorised by a floor that could not decide.
    """
    try:
        authority = deps.capability_floor(envelope)
    except Exception as exc:  # noqa: BLE001 -- the floor fails closed, never open
        return replace(REFUSED, reason=f"floor-crashed:{type(exc).__name__}")
    return authority if isinstance(authority, Authority) else REFUSED


def _denied(deps: GateDeps, ctx: _Ctx, meaning: Optional[Meaning],
            authority: Authority) -> GateResult:
    """RECORD_DENIED, outcome DENIED, return PASS: one denial, wherever it falls.

    A denial is a log line, never a deferral, so it costs the operator nothing:
    the verb is still asked, or still applied, on their next prompt.
    """
    why = authority.reason if not authority.ok else "not-permitted"
    deps.record_denied(ctx.verb, meaning, why)
    return GateResult(Verdict.PASS, Outcome.DENIED, Action.DENY, ctx.verb, ctx.args,
                      reason=f"denied:{why}", evidence=ctx.evidence)


def _unit(n: Any) -> int:
    """INDEPENDENT is 0 or 1 whatever a callable returns; anything else counts 0."""
    return 1 if n == 1 else 0


#: Lean ``promote``: a skill at N >= 2 independent uses, and only with quality.
PROMOTE_AT = 2


def promotes(uses: int, quality: Callable[[], Any]) -> bool:
    """Lean ``promote uses quality``. QUALITY is asked only once N >= 2."""
    return uses >= PROMOTE_AT and bool(quality())


def _apply_bound(prompt: str, envelope: Envelope, deps: GateDeps, ctx: _Ctx,
                 bound: Binding) -> GateResult:
    """A bound verb (clauses 5 and 9).

    A binding fixes MEANING, never AUTHORITY, so the floor is re-checked on
    every use, before anything is counted or applied: a refusal applies
    nothing, counts no use and promotes nothing. Past it there is no meaning
    floor, no council and no question.
    """
    authority = _authority(envelope, deps)
    if not authority.permits(bound.meaning):
        return _denied(deps, ctx, bound.meaning, authority)
    independent = _unit(deps.independent(prompt, bound, envelope))
    binding = deps.record_use(bound, independent)
    if promotes(binding.uses, lambda: deps.quality(binding)):
        deps.promote(binding)  # grants no new capability
    application = deps.apply(binding, envelope, ctx.args)
    return GateResult(Verdict.PASS, Outcome.BOUND, Action.APPLY, ctx.verb, ctx.args,
                      reason="bound", binding=binding, application=application,
                      independent=independent)


def _fresh(prompt: str, envelope: Envelope, deps: GateDeps, ctx: _Ctx) -> GateResult:
    """A verb with no binding: authority first, then evidence, then Koppel."""
    authority = _authority(envelope, deps)  # a crash here is a refusal (clause 9)
    if not authority.ok:
        return _denied(deps, ctx, None, authority)
    ctx.evidence = _meaning_evidence(prompt, deps, ctx)
    return _koppel_loop(envelope, deps, ctx, authority)


def _meaning_evidence(prompt: str, deps: GateDeps, ctx: _Ctx) -> Evidence:
    """FLOOR_EVIDENCE, then one pre-council pass when uncertain. A crash: none."""
    try:
        evidence = deps.floor_evidence(prompt, ctx.verb, ctx.args)
        if not isinstance(evidence, Evidence):
            return NO_EVIDENCE
        if evidence.uncertain and deps.pre_council is not None:
            evidence = tighten(evidence, deps.pre_council(evidence))
        return evidence
    except Exception:  # noqa: BLE001 -- extraction, council, budget: never creates
        return NO_EVIDENCE


def _koppel_loop(envelope: Envelope, deps: GateDeps, ctx: _Ctx,
                 authority: Authority) -> GateResult:
    """At most ``KOPPEL_MAX_ROUNDS`` rounds, counted across invocations."""
    first = max(0, int(deps.rounds_used(ctx.verb))) + 1
    for rnd in range(first, KOPPEL_MAX_ROUNDS + 1):
        answer = deps.koppel(KoppelRequest(ctx.verb, ctx.args, ctx.evidence, rnd))
        kind = getattr(answer, "kind", "unclear")
        if kind == "pending":
            deps.record_round(ctx.verb)
            return GateResult(Verdict.HITL, Outcome.ASKED, Action.ASK, ctx.verb, ctx.args,
                              reason=f"koppel-round-{rnd}", evidence=ctx.evidence,
                              koppel_payload=getattr(answer, "payload", None), round=rnd)
        if kind in ("refined", "deferred"):
            return _settle(answer, envelope, deps, ctx, authority)
        deps.record_round(ctx.verb)
    deps.record_unresolved(ctx.verb, "exhausted")
    return GateResult(Verdict.PASS, Outcome.EXHAUSTED, Action.NONE, ctx.verb, ctx.args,
                      reason="koppel-exhausted", evidence=ctx.evidence)


def _as_meaning(value: Any) -> Optional[Meaning]:
    return value if isinstance(value, Meaning) else None


def _settle(answer: Any, envelope: Envelope, deps: GateDeps, ctx: _Ctx,
            authority: Authority) -> GateResult:
    """A refined or deferred answer. The binding is written LAST (clause 2).

    A refined answer the authority floor permits is RATIFIED -- the operator
    approved this meaning -- never CREATE (Lean ``koppel_never_creates``).
    """
    if answer.kind == "deferred":
        deps.record_unresolved(ctx.verb, "deferred")
        return GateResult(Verdict.PASS, Outcome.DEFERRED, Action.DEFER, ctx.verb, ctx.args,
                          reason="deferred", evidence=ctx.evidence)
    meaning = _as_meaning(getattr(answer, "meaning", None))
    if not authority.permits(meaning):  # the human decides MEANING, the floor AUTHORITY
        return _denied(deps, ctx, meaning, authority)
    binding = deps.verbind(ctx.verb, meaning, envelope)
    application = deps.apply(binding, envelope, ctx.args)
    binding = deps.commit(binding)  # a crash anywhere above leaves no binding
    return GateResult(Verdict.RATIFIED, Outcome.RATIFIED, Action.APPLY, ctx.verb, ctx.args,
                      reason="bound-by-koppel", evidence=ctx.evidence,
                      binding=binding, application=application)


def _answer(verb: str, args: str, answer: Any, envelope: Envelope, deps: GateDeps,
            ctx: _Ctx) -> GateResult:
    ctx.verb, ctx.args = verb, args or ""
    authority = _authority(envelope, deps)
    if not authority.ok:
        return _denied(deps, ctx, _as_meaning(getattr(answer, "meaning", None)), authority)
    if getattr(answer, "kind", None) not in ("refined", "deferred"):
        return GateResult(Verdict.HITL, Outcome.UNANSWERED, Action.NONE, verb, ctx.args,
                          reason="unanswered")
    return _settle(answer, envelope, deps, ctx, authority)


# ── SURFACE: one line, on every path that has a verb ─────────────────────────


def describe_operation(meaning: Meaning) -> str:
    """How an operation reads to a person: ``/skill``, ``/mode m`` or an agent."""
    if meaning.kind == "agent":
        return f"the {meaning.target} agent"
    if meaning.kind == "mode":
        return f"/mode {meaning.target}"
    return f"/{meaning.target}"


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _surface_apply(r: GateResult) -> str:
    meaning = r.binding.meaning
    how = "now bound" if r.outcome is Outcome.RATIFIED else "bound"
    because = _clip(meaning.note) if meaning.note else "your Koppel answer"
    return (f"/{r.verb} -> {how} to {describe_operation(meaning)} because {because}; "
            f"`/verbind refine {r.verb}` to correct")


#: One line per outcome that binds nothing. Formatted with the result's fields.
_SURFACE_LINES = {
    Outcome.ASKED: ("/{verb} is new: a Koppel round will ask what it means "
                    f"(round {{round}} of {KOPPEL_MAX_ROUNDS})"),
    Outcome.DEFERRED: ("/{verb} deferred: treated as text and not asked again; "
                       "`/verbind forget {verb}` to undo"),
    Outcome.DENIED: "/{verb} not applied ({reason}); the prompt runs as plain text",
    Outcome.ALREADY_DEFERRED: ("/{verb} was deferred before: treated as text; "
                               "`/verbind forget {verb}` to be asked again"),
    Outcome.EXHAUSTED: f"/{{verb}} left unbound after {KOPPEL_MAX_ROUNDS} Koppel rounds; treated as text",
}
_SURFACE_DEFAULT = "/{verb}: Verbind decided nothing ({reason}); nothing was bound"


def render_surface(result: GateResult) -> str:
    """The default SURFACE: one non-blocking line, empty when there is no verb."""
    if result.verb is None:
        return ""
    if result.outcome in (Outcome.BOUND, Outcome.RATIFIED) and result.binding is not None:
        return _surface_apply(result)
    line = _SURFACE_LINES.get(result.outcome, _SURFACE_DEFAULT)
    return line.format(verb=result.verb, round=result.round, reason=result.reason)
