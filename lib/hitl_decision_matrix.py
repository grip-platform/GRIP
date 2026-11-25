#!/usr/bin/env python3
"""The HITL decision matrix as code.

``skills/hitl-decision-matrix/SKILL.md`` has described a five-phase flow since
2026-04-18 -- plain-language preamble, batched questions, answer capture,
cascade detection, confirmed persistence -- and until 2026-10-03 nothing
implemented it: the only code in the family was ``lib/hitl_quality_gates.py``,
which checks the SHAPE of a question. Cascade detection and persistence were
narrated, and a narrated step is the one that gets skipped under time pressure.
This module makes the two load-bearing steps mechanical and leaves the asking to
the caller, because the asking is the one part that must go through the harness
primitive (``AskUserQuestion``), not through a library.

What is decided here, and why:

* **Batches of at most three.** The skill says three is the sweet spot and the
  quality gates allow four; this module caps at three (``MAX_BATCH``) and never
  puts a decision in the same batch as one it depends on, so the cascade check
  can rewrite a dependant before it is asked. A caller may lower the cap, never
  raise it.
* **Cascade detection without reading minds.** A reframe is free text and no
  code can tell what assumption it broke. So assumptions are TAGS: a decision's
  default ``settles`` some tags and another decision's default ``assumes`` some.
  An accepted default settles its tags; a reframe or an override settles none
  (unless the caller says which survive). Every not-yet-asked decision whose
  ``assumes`` meets a tag that was expected and is now unsettled is STALE and
  must be revised before it is asked; a revised question is labelled
  ``(revised given D{N} <topic>)`` so the operator knows it is not the original.
  A caller that declares no tags gets no cascade -- honestly, not silently: the
  report says so.
* **Persistence is injected.** The vision-doc append is a pure string this
  module writes when given a path; hypothesis registration goes through a
  ``register_fn`` whose default is ``lib.hypothesis_engine.register``. Only a
  decision that carries a ``HypothesisSpec`` (claim, metric, prediction, measure
  plan) is registered -- the engine refuses unmeasurable claims, and the matrix
  does not invent metrics. A decision the operator worded tentatively is
  registered as a hypothesis with that tentativeness in the claim, never as a
  commitment (an anti-pattern the skill names).

Usage from a session (the asking stays with the harness):

    from hitl_decision_matrix import Matrix, Answer, load_spec, preamble, questions, persist
    m = Matrix(load_spec(Path("spec.json")))
    print(preamble(m))                        # phase 1
    batch = m.batches()[0]                    # phase 2: one batch per AskUserQuestion call
    payload = questions(batch)                # validated by hitl_quality_gates
    ... ask, then for each answer ...
    report = m.record(Answer("D2", "reframe", note="streaming, not a webcam"))   # phases 3 + 4
    print(report.summary())                   # names the later defaults that are now stale
    m.revise("D4", default="...", default_detail="...")  # before D4 is asked
    result = persist(m, vision_doc=Path("plans/vision.md"))  # phase 5, after the operator confirms

CLI (for dogfooding a spec before a sitting):
    python3 lib/hitl_decision_matrix.py spec.json --preamble
    python3 lib/hitl_decision_matrix.py spec.json --batches
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from itertools import chain
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from hitl_quality_gates import validate_questions  # noqa: E402

MAX_BATCH = 3
LOCKED, LOCKED_OVERRIDE, DEFERRED, REFRAME = "locked", "locked (override)", "deferred", "reframe"
KINDS = (LOCKED, LOCKED_OVERRIDE, DEFERRED, REFRAME)


# ── Data types ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Override:
    label: str
    detail: str


@dataclass(frozen=True)
class HypothesisSpec:
    """What the hypothesis engine needs; nothing here is invented by the matrix."""

    claim: str
    metric: str
    prediction: str
    measure_plan: str
    deadline: str = ""


@dataclass
class Decision:
    id: str
    topic: str
    question: str
    default: str
    default_detail: str
    rationale: str
    falsifier: str
    overrides: list[Override] = field(default_factory=list)
    defer_reason: str = "needs more information before locking"
    depends_on: list[str] = field(default_factory=list)
    assumes: set[str] = field(default_factory=set)
    settles: set[str] = field(default_factory=set)
    tentative: bool = False
    hypothesis: HypothesisSpec | None = None
    revised_given: str | None = None

    def header(self) -> str:
        return f"{self.id} {self.topic}"[:12]


@dataclass(frozen=True)
class Answer:
    decision_id: str
    kind: str
    value: str = ""
    note: str = ""
    kept_settles: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown answer kind {self.kind!r}; expected one of {KINDS}")


@dataclass(frozen=True)
class Stale:
    decision: Decision
    because_of: str
    unsettled: frozenset[str]


@dataclass
class CascadeReport:
    answer: Answer
    stale: list[Stale]
    untracked: bool

    @property
    def cascaded(self) -> bool:
        return bool(self.stale)

    def summary(self) -> str:
        if self.untracked:
            return (
                f"{self.answer.decision_id}: {self.answer.kind}; no assumption tags are "
                "declared in this matrix, so no cascade could be detected mechanically."
            )
        if not self.stale:
            return f"{self.answer.decision_id}: {self.answer.kind}; no later default depended on it."
        names = ", ".join(_stale_label(s) for s in self.stale)
        return f"{self.answer.decision_id}: {self.answer.kind}; now stale and must be revised before asking: {names}."


def _stale_label(s: Stale) -> str:
    assumed = ", ".join(sorted(s.unsettled)) or "its default"
    return f"{s.decision.id} (assumed {assumed})"


@dataclass
class PersistResult:
    vision_doc: Path | None
    appended: str
    registered: list[str]
    skipped_unmeasurable: list[str]


# ── Loading ───────────────────────────────────────────────────────────────────


def _decision_from_dict(d: dict) -> Decision:
    hyp = d.get("hypothesis")
    return Decision(
        id=str(d["id"]),
        topic=str(d["topic"]),
        question=str(d["question"]),
        default=str(d["default"]),
        default_detail=str(d.get("default_detail", d["default"])),
        rationale=str(d.get("rationale", "")),
        falsifier=str(d.get("falsifier", "")),
        overrides=[Override(str(o["label"]), str(o["detail"])) for o in d.get("overrides", [])],
        defer_reason=str(d.get("defer_reason", "needs more information before locking")),
        depends_on=[str(x) for x in d.get("depends_on", [])],
        assumes=set(d.get("assumes", [])),
        settles=set(d.get("settles", [])),
        tentative=bool(d.get("tentative", False)),
        hypothesis=HypothesisSpec(**hyp) if hyp else None,
    )


def load_spec(path: Path) -> list[Decision]:
    """A spec is a JSON list of decisions, or an object with a ``decisions`` list."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data["decisions"] if isinstance(data, dict) else data
    decisions = [_decision_from_dict(d) for d in items]
    ids = [d.id for d in decisions]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate decision ids in {path}: {ids}")
    return decisions


# ── The matrix: state, batching, cascade ──────────────────────────────────────


class Matrix:
    """Decisions, the answers so far, and the cascade they cause. Rendering and
    persistence are module functions over a Matrix (see ``preamble``,
    ``questions``, ``persist``)."""

    def __init__(self, decisions: Iterable[Decision], *, batch_size: int = MAX_BATCH) -> None:
        if not 1 <= batch_size <= MAX_BATCH:
            raise ValueError(f"batch_size must be 1..{MAX_BATCH}, got {batch_size}")
        self.decisions: list[Decision] = list(decisions)
        self.batch_size = batch_size
        self.answers: dict[str, Answer] = {}
        self.by_id: dict[str, Decision] = {d.id: d for d in self.decisions}
        unknown = set(chain.from_iterable(d.depends_on for d in self.decisions)) - set(self.by_id)
        if unknown:
            raise ValueError(f"depends_on names unknown decisions: {sorted(unknown)}")

    def unanswered(self) -> list[Decision]:
        return [d for d in self.decisions if d.id not in self.answers]

    def batches(self) -> list[list[Decision]]:
        """Batches of at most ``batch_size`` unanswered decisions, in order, such
        that no decision shares a batch with one it depends on. Greedy and
        stable: a decision waits for the next batch rather than reordering."""
        batches: list[list[Decision]] = []
        current: list[Decision] = []
        current_ids: set[str] = set()
        for d in self.unanswered():
            blocked = bool(current_ids & set(d.depends_on))
            if current and (blocked or len(current) >= self.batch_size):
                batches.append(current)
                current, current_ids = [], set()
            current.append(d)
            current_ids.add(d.id)
        if current:
            batches.append(current)
        return batches

    def record(self, answer: Answer) -> CascadeReport:
        """Record an answer and detect the cascade it causes among the decisions
        not yet asked. Recording a decision twice replaces the earlier answer."""
        if answer.decision_id not in self.by_id:
            raise KeyError(f"no decision {answer.decision_id!r}")
        self.answers[answer.decision_id] = answer
        return CascadeReport(answer=answer, stale=self.cascade_of(answer), untracked=not self.tracks_assumptions())

    def tracks_assumptions(self) -> bool:
        return any(d.assumes or d.settles for d in self.decisions)

    def settled(self) -> set[str]:
        """Tags established so far: an accepted default settles its tags; an
        override or reframe settles only what the caller kept; a deferral
        settles nothing."""
        out: set[str] = set()
        for did, a in self.answers.items():
            out |= _settled_by(self.by_id[did], a)
        return out

    def cascade_of(self, answer: Answer) -> list[Stale]:
        """The unanswered decisions made stale by ``answer``: those whose
        ``assumes`` meets a tag the answered decision no longer settles, or
        that declare ``depends_on`` it. An accepted default cascades nothing."""
        if answer.kind == LOCKED:
            return []
        cause = self.by_id[answer.decision_id]
        lost = _lost_by(cause, answer)
        stale: list[Stale] = []
        for d in self.unanswered():
            hit = d.assumes & lost
            if hit or cause.id in d.depends_on:
                stale.append(Stale(d, because_of=cause.id, unsettled=frozenset(hit)))
        return stale

    def revise(self, decision_id: str, *, default: str, default_detail: str, rationale: str = "",
               falsifier: str = "", given: str | None = None, settles: set[str] | None = None) -> Decision:
        """Rewrite a stale decision's default before it is asked. The question
        is labelled with what it was revised given, so the operator never reads
        a revised option as the original."""
        d = self.by_id[decision_id]
        if decision_id in self.answers:
            raise ValueError(f"{decision_id} was already answered; revise only unasked decisions")
        d.default, d.default_detail = default, default_detail
        if rationale:
            d.rationale = rationale
        if falsifier:
            d.falsifier = falsifier
        if settles is not None:
            d.settles = set(settles)
        d.revised_given = given or self._last_cause_for(d)
        return d

    def _last_cause_for(self, d: Decision) -> str:
        for did in reversed(list(self.answers)):
            a, cause = self.answers[did], self.by_id[did]
            if a.kind != LOCKED and (did in d.depends_on or d.assumes & _lost_by(cause, a)):
                return f"{cause.id} {cause.topic}"
        return "an earlier answer"


def _settled_by(d: Decision, a: Answer) -> set[str]:
    if a.kind == LOCKED:
        return set(d.settles)
    if a.kind in (LOCKED_OVERRIDE, REFRAME):
        return d.settles & set(a.kept_settles)
    return set()


def _lost_by(d: Decision, a: Answer) -> set[str]:
    """Tags the decision's default would have settled and this answer does not."""
    return set(d.settles) - _settled_by(d, a)


# ── Rendering (phases 1 and 2) ────────────────────────────────────────────────


def preamble(m: Matrix) -> str:
    """Phase 1: the plain-language table, one row per decision."""
    lines = ["| # | What we are deciding | Default, in plain words |", "|---|---|---|"]
    lines.extend(f"| {d.id} | {d.question} | {d.default_detail} |" for d in m.decisions)
    lines.append("")
    lines.append(
        "For each one you can accept the default, pick an override, or defer it. "
        "A default comes with the reason for it and the thing that would prove it wrong."
    )
    return "\n".join(lines)


def question_for(d: Decision) -> dict:
    """One decision as an AskUserQuestion question: default (Recommended), up to
    two overrides, Defer. Never mentions 'Other' -- the UI adds it."""
    revised = f" (revised given {d.revised_given})" if d.revised_given else ""
    text = (
        f"{d.id}{revised} -- {d.question} Default: {d.default_detail} "
        f"Falsifier: {d.falsifier} What do you want?"
    )
    options = [{"label": f"{d.default} (Recommended)", "description": d.rationale or d.default_detail}]
    options.extend({"label": o.label, "description": o.detail} for o in d.overrides[:2])
    options.append({
        "label": f"Defer -- {d.defer_reason}",
        "description": "Nothing is persisted for this one; it is re-asked later.",
    })
    return {"question": text, "header": d.header(), "options": options, "multiSelect": False}


def questions(batch: list[Decision]) -> list[dict]:
    """Phase 2: the payload for one batch, validated by the universal quality
    gates (header length, option count, no 'Other' mention...)."""
    payload = [question_for(d) for d in batch]
    validate_questions(payload)
    return payload


# ── Persistence (phase 5) ─────────────────────────────────────────────────────


def _locked_value(d: Decision, a: Answer | None) -> str:
    if a is None or a.kind == DEFERRED:
        return "--"
    if a.kind == LOCKED:
        return d.default
    return a.value or a.note or "(see note)"


def _outcome_row(d: Decision, a: Answer | None) -> str:
    outcome = a.kind if a else "not asked"
    tentative = d.tentative and a is not None and a.kind != DEFERRED
    tag = " (tentative -- registered as a hypothesis, not a commitment)" if tentative else ""
    return f"| {d.id} | {d.question} | {outcome}{tag} | {_locked_value(d, a)} | {d.rationale} / {d.falsifier} |"


def outcomes_table(m: Matrix) -> str:
    rows = ["| # | Decision | Outcome | Locked value | Rationale / falsifier |", "|---|---|---|---|---|"]
    rows.extend(_outcome_row(d, m.answers.get(d.id)) for d in m.decisions)
    return "\n".join(rows)


def _implication_lines(m: Matrix) -> list[str]:
    """One line per decision that was revised because of an earlier answer."""
    return [f"- {d.id} was revised given {d.revised_given}" for d in m.decisions if d.revised_given]


def decisions_locked_section(m: Matrix, *, today: date | None = None, provenance: str = "") -> str:
    day = (today or date.today()).isoformat()
    parts = [f"## Decisions Locked ({day} via HITL decision matrix)", "", outcomes_table(m), ""]
    implications = _implication_lines(m)
    if implications:
        parts += ["### Cascading implications", "", *implications, ""]
    parts.append(
        provenance
        or "Process: batched through lib/hitl_decision_matrix.py (batches of at most three, "
        "dependency-aware; cascade detection on assumption tags; defaults revised before being asked)."
    )
    return "\n".join(parts) + "\n"


def _default_register() -> Callable[..., object]:
    from hypothesis_engine import register  # the engine opens its store on import; load at use

    return register


def _register_one(d: Decision, fn: Callable[..., object]) -> tuple[str | None, str | None]:
    spec = d.hypothesis
    claim = spec.claim if not d.tentative else f"[tentative, {d.id}] {spec.claim}"
    try:
        h = fn(claim=claim, metric=spec.metric, prediction=spec.prediction,
               measure_plan=spec.measure_plan, deadline=spec.deadline)
    except ValueError as exc:  # the engine refusing an unmeasurable claim
        return None, f"{d.id}: {exc}"
    return f"{d.id}: {getattr(h, 'id', h)}", None


def register_hypotheses(m: Matrix, register_fn: Callable[..., object] | None = None) -> tuple[list[str], list[str]]:
    """Register every answered, non-deferred decision that carries a
    HypothesisSpec. Returns (registered, skipped_unmeasurable)."""
    fn = register_fn or _default_register()
    registered: list[str] = []
    skipped: list[str] = []
    for d in m.decisions:
        a = m.answers.get(d.id)
        if a is None or a.kind == DEFERRED or d.hypothesis is None:
            continue
        ok, bad = _register_one(d, fn)
        if ok:
            registered.append(ok)
        if bad:
            skipped.append(bad)
    return registered, skipped


def persist(
    m: Matrix,
    *,
    vision_doc: Path | None = None,
    register_fn: Callable[..., object] | None = None,
    today: date | None = None,
    provenance: str = "",
) -> PersistResult:
    """Phase 5: append the Decisions Locked section to ``vision_doc`` (when
    given) and register hypotheses through ``register_fn`` (default: the
    hypothesis engine). Deferred decisions are never persisted as defaults."""
    section = decisions_locked_section(m, today=today, provenance=provenance)
    if vision_doc is not None:
        vision_doc.parent.mkdir(parents=True, exist_ok=True)
        existing = vision_doc.read_text(encoding="utf-8") if vision_doc.exists() else ""
        sep = "\n" if existing and not existing.endswith("\n\n") else ""
        vision_doc.write_text(existing + sep + section, encoding="utf-8")
    registered, skipped = register_hypotheses(m, register_fn)
    return PersistResult(vision_doc=vision_doc, appended=section, registered=registered, skipped_unmeasurable=skipped)


# ── CLI ───────────────────────────────────────────────────────────────────────


def _print_batches(m: Matrix) -> None:
    for i, batch in enumerate(m.batches(), 1):
        ids = ", ".join(d.id for d in batch)
        print(f"\n# batch {i}: {ids}")
        print(json.dumps(questions(batch), indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a HITL decision-matrix spec: preamble and batches")
    parser.add_argument("spec", type=Path)
    parser.add_argument("--preamble", action="store_true")
    parser.add_argument("--batches", action="store_true", help="print each batch's validated AskUserQuestion payload")
    parser.add_argument("--batch-size", type=int, default=MAX_BATCH)
    args = parser.parse_args(argv)
    m = Matrix(load_spec(args.spec), batch_size=args.batch_size)
    if args.preamble or not args.batches:
        print(preamble(m))
    if args.batches:
        _print_batches(m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
