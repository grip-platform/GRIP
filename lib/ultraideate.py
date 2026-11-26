#!/usr/bin/env python3
"""/ultraideate -- many ideas from outside models, ranked by Jev, picked by the operator.

Three separable steps (the plan's snug-questing-quill.md, Phase 5.5):

  DIVERGE  -- ask GRIP's sovereign council for ideas on a topic, criticism
              suspended. The council has no Anthropic seat by design
              (lib.sovereign_council.DEFAULT_SPEAKERS); this module never adds one.
  CONVERGE -- Jev (TypeSafe's decision model, through HAL ``decide``) scores
              every idea against five operator criteria as ``score`` questions,
              batched under Jev's measured token ceilings.
  COMBINE  -- code, never a model, combines the five scores with operator
              weights into one ranking.

Each step is a plain function taking its outside dependency as a parameter
(``council=`` / ``decide=``), so a test can pass a stub and never reach a
network. Data limit (the operator's words, 2026-10-10): internal and public
text only -- GRIP/HAL code, docs, logs, public legal sources. Never personal
matters, WhatsApp content, children's data, secrets or client documents. Every
send to the council or to Jev goes through the decision-model privacy floor
first (``lib.decision_privacy.may_send``); the council's own redaction shield
runs on top of that, unchanged.

Jev ranks; it does not judge truth. Ideas come from outside models, and their
text could steer Jev (a published benchmark flipped 12.1% of decisions with one
appended opinion) -- so CONVERGE's ranking is advice, and the operator's pick
in ``record_picks`` is the decision this module actually measures.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB.parent) not in sys.path:
    sys.path.insert(0, str(_LIB.parent))

# --------------------------------------------------------------------------
# Criteria, the question shape, and Jev's measured limits
# --------------------------------------------------------------------------

#: The five operator-weighted criteria CONVERGE scores every idea against.
CRITERIA: Tuple[str, ...] = ("novelty", "delight", "measurability", "feasibility", "data_safety")

#: One complete sentence per criterion, so each question reads naturally once
#: the idea text is appended under it. Each asks only about what Jev is sent --
#: the topic and the idea -- and common knowledge. "Compared to what GRIP
#: already does" and "GRIP's existing tools" asked about a product no request
#: describes; on run 2e9350ef223b's 49 ideas those two criteria settled 1
#: reading of 49 each, in each of three repeats (2026-11-24).
_CRITERION_TEXT: Dict[str, str] = {
    "novelty": ("Rate how NEW or unexpected this idea is, compared to the usual, obvious "
                "answers to the brainstorm topic."),
    "delight": "Rate how much a user would enjoy or feel delighted by this idea.",
    "measurability": ("Rate how easy it would be to define a clear, falsifiable measure of "
                      "whether this idea actually works."),
    "feasibility": ("Rate how practical this idea is to build or carry out with tools and "
                    "skills that are commonly available."),
    "data_safety": ("Rate how well this idea respects the data limit: internal and public text "
                    "only, never personal, client or children's data."),
}

#: How many levels every criterion is rated on. _read_score reads every answer
#: against this count, so each scale below has exactly this many levels.
LEVEL_COUNT = 5

#: Each criterion's own levels, lowest first. Jev reads a Score's levels as
#: "ordered descriptions" (docs.typesafe.ai/api.md). One generic scale that all
#: five criteria shared ("not at all" ... "very much") left 181 of 245 readings
#: under ASK_BELOW on run 2e9350ef223b's 49 ideas, in each of three repeats;
#: these scales, with the wording above, left 104, 100 and 102 (2026-11-24).
#: The scores moved no more between repeats than before, but more readings now
#: sit near ASK_BELOW and cross it between repeats, which moves the top of the
#: ranking (GRIP#9478).
_CRITERION_LEVELS: Dict[str, Tuple[str, ...]] = {
    "novelty": ("an obvious idea most people would suggest first", "a common idea with a small twist",
                "a less common idea some people would think of", "an unusual idea few people would think of",
                "a surprising idea that is genuinely new"),
    "delight": ("nobody would notice or care", "mildly useful, but nobody would enjoy it", "pleasant to use",
                "fun, and people would tell others about it", "a joy that people would use again and again"),
    "measurability": ("impossible to tell whether it works", "only a vague impression would show whether it works",
                      "a rough count or check would show whether it works",
                      "a clear number would show whether it works",
                      "a simple pass-or-fail test would show whether it works"),
    "feasibility": ("not possible with anything that exists today",
                    "possible only with major new work or special hardware",
                    "possible with a moderate amount of work", "easy with common tools and a little work",
                    "works almost at once with what is already there"),
    "data_safety": ("needs personal, client or children's data", "likely to touch personal data",
                    "might touch personal data unless it is designed with care",
                    "touches almost no personal data", "uses only internal or public data"),
}

#: Below this confidence a Score answer is reported as uncertain, never as the
#: model's verdict (mirrors lib.prompt_routing_score.ASK_BELOW).
ASK_BELOW = 0.5
#: What an unscored criterion counts as when ranking: the middle of the scale.
UNSCORED_VALUE = 0.5

#: Jev's measured ceilings (docs/decide.md, HAL 2026-10-10): a state over 32k
#: tokens, or a request over 64k, is refused outright with no usage recorded.
MAX_REQUEST_TOKENS = 64_000
MAX_STATE_TOKENS = 32_000
#: Rough chars-per-token ratio, the same rule of thumb lib/prefix_compaction.py
#: and others use.
_CHARS_PER_TOKEN = 4
#: A live 664-idea run (docs/measurements/2026-10-12-ultraideate-live-run.md)
#: was refused outright as over_budget: a 1,514-question batch this module
#: estimated at the 64k ceiling measured 40,178-24,068 REAL input tokens for
#: 500-300-question slices of it -- 1.92-1.94x this estimator's own count for
#: the same text. Short, hyphenated, quote-heavy fragments ("Use
#: \"pre-set agenda\"") tokenize less efficiently than 4 chars/token assumes.
#: This factor corrects for it with margin above the measured ratio; it is
#: not a universal tokenizer constant, only a calibration against THIS
#: content shape plus a safety margin for others.
_TOKEN_ESTIMATE_SAFETY_FACTOR = 2.5

#: A split-out idea shorter than this is noise (a stray "Sure," or a blank
#: bullet), not a real idea.
MIN_IDEA_CHARS = 12

#: Either switch turns CONVERGE off: the operator's, and the test suite's
#: fence for every decision-model call (tests/conftest.py).
_OFF = (("GRIP_ULTRAIDEATE", "0"), ("GRIP_CLASSIFY_NO_DECIDE", "1"))

Decide = Callable[[str, dict], Optional[dict]]
Council = Callable[[str], list]


def enabled() -> bool:
    """CONVERGE's switch. DIVERGE has none of its own -- it is gated only by
    the privacy floor and by whatever council callable is injected."""
    return not any(os.environ.get(name, "") == value for name, value in _OFF)


def _floor(text: str, cwd: Optional[str]) -> Tuple[bool, str]:
    try:
        from lib.decision_privacy import may_send
        return may_send(text, cwd=cwd)
    except Exception:  # noqa: BLE001 -- no floor, no send
        return False, "privacy_floor_unavailable"


def _estimate_tokens(text) -> int:
    """A deliberately conservative upper bound, not a real tokenizer -- see
    _CHARS_PER_TOKEN and _TOKEN_ESTIMATE_SAFETY_FACTOR."""
    chars = len(str(text))
    return max(1, int(chars / _CHARS_PER_TOKEN * _TOKEN_ESTIMATE_SAFETY_FACTOR))


# --------------------------------------------------------------------------
# DIVERGE
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Idea:
    """One brainstormed idea: short text, and every speaker who proposed it."""
    id: str
    text: str
    proposed_by: Tuple[str, ...]


@dataclass(frozen=True)
class DivergeResult:
    """DIVERGE's outcome, or why there is none. ``skipped`` names a reason;
    an empty, non-skipped result means the council sat but proposed nothing
    usable."""
    ideas: Tuple[Idea, ...] = ()
    speakers_ok: int = 0
    speakers_total: int = 0
    skipped: str = ""


def _diverge_prompt(topic: str) -> str:
    return (
        "Brainstorm as many distinct ideas as you can for the topic below. "
        "Criticism is suspended: this is a divergent brainstorm, not an "
        "evaluation -- do not rank, filter or critique your ideas, a later "
        "step scores and ranks them. List each idea as its own short bullet "
        "point, one or two sentences, as many as you can think of.\n\n"
        f"TOPIC: {topic}"
    )


def _default_council(topic: str) -> list:
    from lib.sovereign_council import fan_out, DEFAULT_SPEAKERS
    results, _wall = fan_out(_diverge_prompt(topic), list(DEFAULT_SPEAKERS))
    return results


#: A bullet/numbered list marker. The plan's own council output shows bold
#: labels riding INSIDE a dash bullet ("- **alpha. Title.** description"),
#: which this already strips correctly, keeping the bold text as part of the
#: idea -- there is deliberately no separate dash-less "**Label.**" rule: an
#: earlier version tried to strip a bold span on its own and, because the
#: idea's own title sits inside that same span, stripped the title along with
#: the markup. A bold header with no leading dash/number instead falls
#: through to the no-markup case below (the whole answer as one idea).
_BULLET = re.compile(r"^\s*(?:[-*•]\s+|\d{1,3}[.)]\s+)")
#: A reasoning block a speaker answers with before its list (DeepSeek's
#: reasoner, GRIP#8824): its bullets are the speaker thinking, never ideas. A
#: block the token limit cut off has no closing tag and runs to the end of the
#: answer; it is dropped to the end, never promoted (Codex on GRIP#8846).
_THINK = re.compile(r"<think(?:ing)?>.*?(?:</think(?:ing)?>|\Z)", re.IGNORECASE | re.DOTALL)
_WORD = re.compile(r"[a-z0-9]{2,}")


def _is_echo(text: str, topic: str) -> bool:
    """An item that adds no word the topic did not already contain is a
    restatement of the prompt, not an idea: the fragments a speaker echoes
    back while it reasons ("unanswered questions", "deterministic gate:
    condition that triggers action" on the 2026-10-29 run, GRIP#8824). Words
    are two characters or longer, so an acronym-only topic ("AI vs ML vs UX")
    is covered too (Codex on GRIP#8846)."""
    words = set(_WORD.findall(text.lower()))
    return bool(words) and words <= set(_WORD.findall(topic.lower()))


def _split_ideas(text: str) -> List[str]:
    """One idea per bullet/numbered item in ``text``; a continuation line joins
    the item above it. A speaker who wrote no list markup at all is read as
    ONE idea -- its whole answer -- never dropped. A reasoning block
    (``<think>…</think>``) is dropped before any of that."""
    text = _THINK.sub("", text)
    ideas: List[str] = []
    current: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if _BULLET.match(line):
            if current:
                ideas.append(" ".join(current).strip())
            current = [_BULLET.sub("", line, count=1).strip()]
        elif not line:
            if current:
                ideas.append(" ".join(current).strip())
            current = []
        elif current:
            current.append(line)
    if current:
        ideas.append(" ".join(current).strip())
    ideas = [i for i in ideas if len(i) >= MIN_IDEA_CHARS]
    return ideas or ([text.strip()] if text.strip() else [])


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def dedupe_ideas(raw: Sequence[Tuple[str, str]]) -> List[Idea]:
    """Flatten ``(speaker, idea_text)`` pairs into one Idea per distinct text,
    in order of first appearance. The same (normalised) text from a SECOND
    speaker credits that speaker on the existing Idea rather than duplicating
    it; a repeat from the SAME speaker is dropped outright."""
    by_key: Dict[str, Idea] = {}
    order: List[str] = []
    for speaker, text in raw:
        text = text.strip()
        key = _normalise(text)
        if not text or not key:
            continue
        if key in by_key:
            existing = by_key[key]
            if speaker not in existing.proposed_by:
                by_key[key] = Idea(existing.id, existing.text, existing.proposed_by + (speaker,))
            continue
        by_key[key] = Idea(f"idea-{len(order) + 1}", text, (speaker,))
        order.append(key)
    return [by_key[k] for k in order]


def diverge(topic: str, *, cwd: Optional[str] = None, council: Council = _default_council) -> DivergeResult:
    """DIVERGE: ask the sovereign council for ideas, criticism suspended.

    Never calls the council on text the privacy floor refuses. A council that
    is not at full quorum (the subscription Astra seat is routinely out of
    quota) still returns whatever its live speakers answered -- a dead seat
    among four is not a reason to produce nothing.
    """
    allowed, why = _floor(topic, cwd)
    if not allowed:
        return DivergeResult(skipped=f"privacy_floor:{why}")
    results = council(topic)
    ok = [r for r in results if r.get("ok")]
    raw = [(r.get("name", ""), text) for r in ok for text in _split_ideas(r.get("text", ""))
           if not _is_echo(text, topic)]
    ideas = dedupe_ideas(raw)
    return DivergeResult(tuple(ideas), len(ok), len(results), skipped="" if ideas else "no_ideas")


def merge_seeds(seeds: Sequence[Idea], council: Sequence[Idea]) -> List[Idea]:
    """The operator's seeds first, then DIVERGE's ideas, as one list that CONVERGE scores together.

    A council idea whose normalised text matches a seed is the same idea: its
    speakers are credited on the seed, which keeps its own id, place and
    proposers, and the council copy is not listed twice. A seed repeated in the
    file merges into its first copy the same way, and a blank seed is dropped.
    Seeds are ``seed-N`` and council ideas ``idea-N``, so the two never share an
    id and a pick names exactly one idea.
    """
    merged: List[Idea] = []
    where: Dict[str, int] = {}
    for idea in (*seeds, *council):
        key = _normalise(idea.text)
        if not key:
            continue
        if key not in where:
            where[key] = len(merged)
            merged.append(idea)
            continue
        first = merged[where[key]]
        extra = tuple(p for p in idea.proposed_by if p not in first.proposed_by)
        merged[where[key]] = Idea(first.id, first.text, first.proposed_by + extra)
    return merged


# --------------------------------------------------------------------------
# CONVERGE
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Scored:
    """One idea, with whatever criteria Jev actually settled.

    ``scores`` and ``confidences`` hold only criteria that were answered with
    confidence >= ASK_BELOW; everything else -- never scored, answered but
    uncertain, refused by the floor -- is named in ``skipped`` instead of
    guessed. Mutated in place by CONVERGE as batches come back; frozen only
    stops a caller from rebinding the Idea itself.
    """
    idea: Idea
    scores: Dict[str, float] = field(default_factory=dict)
    confidences: Dict[str, float] = field(default_factory=dict)
    models: Dict[str, str] = field(default_factory=dict)
    skipped: Dict[str, str] = field(default_factory=dict)

    def unscored(self) -> Tuple[str, ...]:
        return tuple(c for c in CRITERIA if c not in self.scores)


def _score_question(idea: Idea, criterion: str) -> dict:
    return {"type": "score", "instructions": f"{_CRITERION_TEXT[criterion]}\n\nIDEA: {idea.text}",
           "criteria": list(_CRITERION_LEVELS[criterion])}


def _question_tokens(question: dict) -> int:
    return (_estimate_tokens(question.get("instructions", ""))
           + sum(_estimate_tokens(c) for c in question.get("criteria", [])))


def batch_questions(state: str, questions: Dict[str, dict], *,
                    max_request_tokens: int = MAX_REQUEST_TOKENS,
                    max_state_tokens: int = MAX_STATE_TOKENS) -> List[Dict[str, dict]]:
    """Split ``questions`` into the fewest batches that respect Jev's measured
    ceilings: each batch's ``state`` + its own questions must stay at or under
    ``max_request_tokens``, and ``state`` plus any ONE question must stay at
    or under ``max_state_tokens`` (the stricter per-state ceiling HAL measured,
    kept here per-question as a safety margin rather than only summed once).

    Raises ValueError when ``state`` alone, or a single question alongside it,
    could never be sent regardless of batching -- there is no smaller batch
    that would help.
    """
    state_tokens = _estimate_tokens(state)
    if state_tokens > max_state_tokens:
        raise ValueError(f"state is ~{state_tokens} estimated tokens, over the {max_state_tokens} ceiling")
    batches: List[Dict[str, dict]] = []
    current: Dict[str, dict] = {}
    current_tokens = 0
    for qid, question in questions.items():
        qtok = _question_tokens(question)
        if state_tokens + qtok > max_state_tokens:
            raise ValueError(f"question {qid!r} (~{qtok} tokens) plus state exceeds {max_state_tokens}")
        if current and state_tokens + current_tokens + qtok > max_request_tokens:
            batches.append(current)
            current, current_tokens = {}, 0
        current[qid] = question
        current_tokens += qtok
    if current:
        batches.append(current)
    return batches


def _read_score(answer) -> Optional[Tuple[float, float]]:
    """``(value in 0..1, confidence)`` from a Score answer, or None when it is
    malformed or does not rate along LEVEL_COUNT levels."""
    if not isinstance(answer, dict) or answer.get("type") != "score":
        return None
    probs = answer.get("probabilities")
    if not isinstance(probs, dict) or set(probs) != {str(i) for i in range(LEVEL_COUNT)}:
        return None
    score, confidence = answer.get("score"), answer.get("confidence")
    numbers = list(probs.values()) + [confidence]
    ok_numbers = all(isinstance(n, (int, float)) and not isinstance(n, bool) and 0 <= n <= 1 for n in numbers)
    ok_score = isinstance(score, (int, float)) and not isinstance(score, bool) and 0 <= score <= LEVEL_COUNT - 1
    if not ok_numbers or not ok_score:
        return None
    return score / (LEVEL_COUNT - 1), float(confidence)


def _default_decide(state: str, questions: dict) -> Optional[dict]:
    from lib.hal_llm_adapter import _query_grip_decide
    return _query_grip_decide(state, questions, timeout_s=20.0)


def _settle_one(scored: Scored, criterion: str, answer, model: str) -> None:
    read = _read_score(answer)
    if read is None:
        scored.skipped[criterion] = "no_answer"
        return
    value, confidence = read
    if confidence < ASK_BELOW:
        scored.skipped[criterion] = "uncertain"
        return
    scored.scores[criterion] = value
    scored.confidences[criterion] = confidence
    if model:
        scored.models[criterion] = model


def _mark_batch_skipped(batch: Dict[str, dict], results: Dict[str, Scored], reason: str) -> None:
    for qid in batch:
        idea_id, criterion = qid.rsplit(":", 1)
        results[idea_id].skipped.setdefault(criterion, reason)


def _answer_batch(state: str, batch: Dict[str, dict], results: Dict[str, Scored], *,
                  cwd: Optional[str], decide: Decide) -> None:
    text = state + " " + " ".join(q["instructions"] for q in batch.values())
    allowed, why = _floor(text, cwd)
    if not allowed:
        _mark_batch_skipped(batch, results, f"privacy_floor:{why}")
        return
    try:
        result = decide(state, batch)
    except Exception:  # noqa: BLE001 -- advisory; a dead batch leaves its ideas unscored
        result = None
    ok = isinstance(result, dict) and result.get("ok")
    if not ok:
        # The server's own reason (over_budget, spend_refused, rate_limited, ...)
        # is kept rather than collapsed into one generic label -- it is what
        # identified the real cause of the 664-idea live run's first failure.
        # Settled here directly: _settle_one's own generic "no_answer" would
        # otherwise win first via setdefault, since every answers.get(qid)
        # is None when there are no real answers to read.
        reason = result.get("reason") if isinstance(result, dict) else ""
        _mark_batch_skipped(batch, results, f"no_answer:{reason}" if reason else "no_answer")
        return
    answers = result.get("answers") if isinstance(result.get("answers"), dict) else {}
    model = str(result.get("model") or "")
    for qid in batch:
        idea_id, criterion = qid.rsplit(":", 1)
        _settle_one(results[idea_id], criterion, answers.get(qid), model)


def converge(ideas: Sequence[Idea], topic: str, *, cwd: Optional[str] = None,
            decide: Optional[Decide] = None) -> List[Scored]:
    """CONVERGE: Jev's Score for every (idea, criterion) pair, batched under
    the token ceilings. Off (``enabled()`` false), every idea comes back fully
    unscored rather than guessed at. ``decide`` is resolved at call time, so a
    test's stand-in for ``_default_decide`` reaches the CLI path too."""
    decide = decide if decide is not None else _default_decide
    results: Dict[str, Scored] = {idea.id: Scored(idea) for idea in ideas}
    if not enabled():
        for scored in results.values():
            scored.skipped.update({c: "converge_off" for c in CRITERIA})
    if not enabled() or not ideas:
        return list(results.values())
    state = f"Brainstorm topic: {topic}"
    questions = {f"{idea.id}:{c}": _score_question(idea, c) for idea in ideas for c in CRITERIA}
    try:
        batches = batch_questions(state, questions)
    except ValueError as exc:
        _mark_batch_skipped(questions, results, f"over_budget:{exc}")
        return list(results.values())
    for batch in batches:
        _answer_batch(state, batch, results, cwd=cwd, decide=decide)
    return list(results.values())


# --------------------------------------------------------------------------
# COMBINE
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Ranked:
    rank: int
    scored: Scored
    combined: float


def operator_weights(path: Optional[Path] = None) -> Tuple[Dict[str, float], str]:
    """The operator's weight for each of CRITERIA, and how they were obtained.

    ``/hitl-decision-matrix`` (skills/hitl-decision-matrix) persists a locked
    decision as markdown prose, and optionally a hypothesis-engine entry -- as
    of this writing (checked against lib/hitl_decision_matrix.py, 2026-10-12)
    it has no per-criterion NUMERIC store a caller can read back:
    ``Decision.default`` is a free-text string, not a weight, and persistence
    is a vision-doc append, not a queryable value. So this reads the small
    JSON file below, which an operator who has locked these five criteria
    through that skill (or by hand) writes as ``{"novelty": 2.0, ...}``; until
    one exists every criterion is weighted equally, and the caller is told so
    plainly rather than left to assume a real matrix was consulted.
    """
    from lib.claude_dir import get_claude_dir
    source = Path(path) if path is not None else (get_claude_dir() / "state" / "decision-weights" / "ultraideate.json")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {c: 1.0 for c in CRITERIA}, "equal_weights_no_matrix_found"
    weights = {c: float(raw[c]) for c in CRITERIA
              if isinstance(raw, dict) and isinstance(raw.get(c), (int, float))
              and not isinstance(raw.get(c), bool) and raw[c] > 0}
    if set(weights) != set(CRITERIA):
        return {c: 1.0 for c in CRITERIA}, "equal_weights_incomplete_matrix"
    return weights, "persisted_matrix"


def _combined_value(scored: Scored, weights: Dict[str, float]) -> float:
    have = [c for c in CRITERIA if c in scored.scores]
    denom = sum(weights[c] for c in have)
    return (sum(weights[c] * scored.scores[c] for c in have) / denom) if denom > 0 else 0.0


def _ranking_value(scored: Scored, weights: Dict[str, float]) -> float:
    """The value an idea ranks on: every criterion counts, and an unscored one counts at the scale's midpoint.

    The mean of the scored criteria alone cannot rank: on shortlist a42e424e9e14
    (2026-11-01) an idea scored on data safety alone took 11 of the top 12 places,
    above ideas scored on more criteria. The midpoint rewards and punishes nothing.
    """
    total = sum(weights[c] for c in CRITERIA)
    return sum(weights[c] * scored.scores.get(c, UNSCORED_VALUE) for c in CRITERIA) / total


def combine(scored: Sequence[Scored], weights: Dict[str, float]) -> List[Ranked]:
    """Rank ideas highest first on ``_ranking_value``, and report the weighted mean of what was scored.

    Ties -- including an idea with every criterion unscored, whose combined
    value is 0.0 by construction -- break on DIVERGE's original order: the
    idea found first keeps the earlier rank. An idea with at least one real
    score always outranks one with none, even on a tied combined value, so a
    genuinely low score is never confused with "nothing was measured".
    """
    def sort_key(pair):
        index, s = pair
        return (0 if s.scores else 1, -_ranking_value(s, weights), index)

    ordered = sorted(enumerate(scored), key=sort_key)
    return [Ranked(i + 1, s, _combined_value(s, weights)) for i, (_, s) in enumerate(ordered)]


# --------------------------------------------------------------------------
# Picks log + the measure plan
# --------------------------------------------------------------------------

def _picks_log_path() -> Path:
    from lib.claude_dir import get_claude_dir
    return get_claude_dir() / "logs" / "ultraideate-picks.jsonl"


def _shortlists_log_path() -> Path:
    from lib.claude_dir import get_claude_dir
    return get_claude_dir() / "logs" / "ultraideate-shortlists.jsonl"


def record_shortlist(topic: str, ranked: Sequence[Ranked], *, log_path: Optional[Path] = None,
                     shuffled: bool = False) -> str:
    """Append one shortlist as it is shown to the operator and return its run id.

    One line per `rank` run: ``ts``, ``run_id``, a digest of the topic (never the
    topic's words, which are the operator's), the ranked ids, how many of the
    run's criterion readings Jev settled out of how many it was asked -- the only
    record of a whole run's coverage, since ``rank --json`` shows the top ideas
    alone -- and ``shown_order``: "shuffled" when ``rank --shuffle`` displayed the
    top ideas in random order instead of Jev's own rank, else "ranked". Without
    this tag a shuffled round and a ranked round are indistinguishable in the log,
    and the measure cannot separate Jev's ranking from the operator favouring
    whatever is shown first (GRIP#9471). A later picks row
    carrying the same ``run_id`` closes it; a shortlist with no such row is a round
    the operator never came back to, which the always-on tick (``lib/grip_tick.py``)
    reports after a day (GRIP#8793). Advisory: never raises into the rank flow, and
    the run id is returned even when the line could not be written. A ranked set in
    which nothing was scored is not a shortlist: nothing is written and the run id is
    empty, so the tick never escalates a round that ranked by divergence order alone
    (GRIP#8824)."""
    import uuid
    scored_ideas = sum(1 for r in ranked if r.scored.scores)
    if not scored_ideas:
        return ""
    run_id = uuid.uuid4().hex[:12]
    path = Path(log_path) if log_path is not None else _shortlists_log_path()
    _append_jsonl(path, {"ts": time.time(), "run_id": run_id, "topic_digest": _topic_digest(topic),
                         "ranked_ids": [r.scored.idea.id for r in ranked], "scored_ideas": scored_ideas,
                         "settled_readings": sum(len(r.scored.scores) for r in ranked),
                         "readings": len(ranked) * len(CRITERIA),
                         "shown_order": "shuffled" if shuffled else "ranked"})
    return run_id


def _topic_digest(topic: str) -> str:
    """The short digest a shortlist row keeps in place of the topic's words."""
    import hashlib
    return hashlib.sha256(topic.encode("utf-8")).hexdigest()[:8]


def _append_jsonl(path: Path, row: dict) -> bool:
    """Append one JSON line; False, never an exception, when it could not be written."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        return False
    return True


def _jsonl_rows(path: Path) -> List[dict]:
    """Every JSON object line of ``path``; a missing file or an unreadable line contributes nothing."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _picks_row(topic: str, ranked_ids: Sequence[str], picked_ids: Sequence[str], k: int, run_id,
              shown_order: str = "ranked") -> dict:
    return {"ts": time.time(), "topic": topic, "k": int(k), "run_id": run_id,
            "ranked_ids": list(ranked_ids), "picked_ids": list(picked_ids), "shown_order": shown_order}


def record_picks(topic: str, ranked: Sequence[Ranked], picked_ids: Sequence[str], *,
                 k: int, log_path: Optional[Path] = None, run_id: Optional[str] = None,
                 shown_order: str = "ranked") -> None:
    """Append one picking session: the full ranked order, which ids the
    operator picked, the top-k size that session was judged against, the
    ``run_id`` of the shortlist it answers (from ``record_shortlist`` or the
    rank command's output), which is what marks that shortlist as picked from,
    and ``shown_order`` ("ranked" or "shuffled") so ``report`` can separate
    Jev's ranking from position bias (GRIP#9471). An EMPTY ``run_id`` is a run
    that scored nothing and recorded no shortlist
    (GRIP#8824): there is nothing to pick from, so nothing is written, and the
    precision measure never sees a row ranked by arrival order. Advisory
    bookkeeping -- never raises into the operator's pick flow. A caller in
    another process than the rank run uses ``record_picks_for_run`` instead."""
    if run_id == "":
        return
    path = Path(log_path) if log_path is not None else _picks_log_path()
    _append_jsonl(path, _picks_row(topic, [r.scored.idea.id for r in ranked], picked_ids, k, run_id, shown_order))


def record_picks_for_run(topic: str, run_id: str, picked_ids: Sequence[str], *, k: int,
                         shortlists_path: Optional[Path] = None, log_path: Optional[Path] = None) -> str:
    """Record a pick made from shortlist ``run_id``, against the FULL ranked order its row holds.

    For a caller that shelled out to ``rank`` and so holds no ``Ranked`` list: it
    used to rebuild one by hand, and 3 of the 23 picks rows that name a recorded
    shortlist (logs as of 2026-11-24) carry a shorter ranked order than that
    shortlist -- 10 ids of 281 on run 81baf51511c1, whose random baseline then
    read 5/10 instead of 5/281. Here the order is copied from the shortlist row,
    and so is its ``shown_order`` tag (GRIP#9471) -- the picks row must carry the
    same "ranked"/"shuffled" value the shortlist did, or ``report``'s breakdown
    by that tag would be checking the wrong thing. Nothing is written, and the
    reason is returned, when the run id has no
    shortlist, the topic's digest differs from the one that shortlist was ranked
    for, a picked id is not in it, the run already has a picks row (a second row
    would count one session twice in ``precision_at_k``), ``k`` is below 1, or
    the write fails. Returns "" when the row was written. An empty pick is a
    valid answer and is recorded."""
    if k < 1:
        return "k must be at least 1"
    ranked_ids, shown_order, why = _shortlist_order(run_id, topic, Path(shortlists_path)
                                                    if shortlists_path is not None else _shortlists_log_path())
    if why:
        return why
    known = set(ranked_ids)
    unknown = [p for p in picked_ids if p not in known]
    if unknown:
        return "not in this shortlist: " + ", ".join(unknown)
    picks_path = Path(log_path) if log_path is not None else _picks_log_path()
    return _append_picks_once(picks_path, run_id, _picks_row(topic, ranked_ids, picked_ids, k, run_id, shown_order))


#: How long a picks write waits for another writer to release the log, in seconds.
_PICKS_LOCK_TIMEOUT_S = 5.0


def _append_picks_once(picks_path: Path, run_id: str, row: dict) -> str:
    """Append ``row`` unless the log already holds a row for ``run_id``, with the check and the write under one
    lock on the log, so two sessions recording the same run at once cannot both write (Codex and the council on
    GRIP#9364). Returns "" when written, else the reason nothing was."""
    if str(_LIB) not in sys.path:
        sys.path.append(str(_LIB))  # lib/file_lock.py imports its sibling modules bare
    from file_lock import FileLock, LockTimeoutError
    try:
        picks_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(picks_path, timeout=_PICKS_LOCK_TIMEOUT_S, sidecar=True):
            if any(r.get("run_id") == run_id for r in _jsonl_rows(picks_path)):
                return f"run {run_id} already has a picks row"
            if not _append_jsonl(picks_path, row):
                return f"could not write {picks_path}"
    except (OSError, LockTimeoutError) as exc:
        return f"could not lock {picks_path}: {exc}"
    return ""


def _shortlist_order(run_id: str, topic: str, path: Path) -> Tuple[List[str], str, str]:
    """The ranked ids of shortlist ``run_id`` and its ``shown_order`` tag, with "" for the reason; or no
    ids, "ranked", and the reason a pick cannot be checked against it."""
    rows = [r for r in _jsonl_rows(path) if run_id and r.get("run_id") == run_id]
    if not rows:
        return [], "ranked", f"no shortlist has run id {run_id!r}"
    if rows[-1].get("topic_digest") != _topic_digest(topic):
        return [], "ranked", "the topic differs from the one this shortlist was ranked for"
    return [str(i) for i in rows[-1].get("ranked_ids") or []], rows[-1].get("shown_order") or "ranked", ""


def _mean(values: Sequence[float]) -> Optional[float]:
    values = list(values)
    return sum(values) / len(values) if values else None


def _row_precision(row: dict) -> dict:
    """One session's precision at k, its random baseline, and whether the round was shown "ranked" (Jev's
    own order) or "shuffled" (GRIP#9471: a row logged before that tag existed carries no such field and
    reads as "ranked", the only mode that existed then). A run with fewer ideas than k is judged at its
    own length: otherwise picking the only idea of a one-idea run read precision 0.2 against a baseline of 1.0
    (Codex on GRIP#9364), when a run that short cannot rank better than chance at all."""
    ranked_ids = row.get("ranked_ids") or []
    k = min(int(row.get("k") or 0), len(ranked_ids))
    picked_ids = set(row.get("picked_ids") or [])
    precision = (len(set(ranked_ids[:k]) & picked_ids) / k) if k else None
    random_baseline = (len(picked_ids) / len(ranked_ids)) if ranked_ids else None
    return {"topic": row.get("topic", ""), "k": k, "precision": precision, "random_baseline": random_baseline,
           "shown_order": row.get("shown_order") or "ranked"}


def _summarise(rows: Sequence[dict]) -> dict:
    """Rows, mean precision@k, mean random baseline and their margin for one group of ``_row_precision``
    rows. Handles an empty group: every mean is None, never a division by zero."""
    precisions = [r["precision"] for r in rows if r["precision"] is not None]
    baselines = [r["random_baseline"] for r in rows if r["random_baseline"] is not None]
    mean_p, mean_b = _mean(precisions), _mean(baselines)
    return {"rows": len(rows), "mean_precision_at_k": mean_p, "mean_random_baseline": mean_b,
           "margin": (mean_p - mean_b) if (mean_p is not None and mean_b is not None) else None}


def precision_at_k(log_path: Optional[Path] = None) -> dict:
    """Precision@k of Jev's ranking against what the operator actually picked,
    one row per picking session, alongside the RANDOM baseline (picked_count /
    ranked_count) the pre-registered hypothesis is measured against: that
    fraction is the expected precision@k of a uniformly random ordering of the
    same ideas, for any k. Handles an empty or missing log: ``rows`` is 0 and
    every mean is None, never a division by zero.

    ``by_shown_order`` breaks the same two numbers down by whether that round
    was displayed in Jev's own rank or shuffled (``rank --shuffle``): the
    combined figure above cannot tell Jev's ranking apart from the operator
    favouring whatever is shown first, because every round used to be shown in
    Jev's order. If the "shuffled" group's precision stays near its own
    margin, picks are tracking Jev's rank; if it collapses toward its own
    random baseline, picks were tracking screen position instead (GRIP#9471).
    """
    path = Path(log_path) if log_path is not None else _picks_log_path()
    rows = [_row_precision(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()
           if line.strip()] if path.exists() else []
    by_shown_order = {mode: _summarise([r for r in rows if r["shown_order"] == mode])
                      for mode in ("ranked", "shuffled")}
    return {**_summarise(rows), "per_row": rows, "by_shown_order": by_shown_order}


# --------------------------------------------------------------------------
# Sealing the ranked set
# --------------------------------------------------------------------------

def _ranked_entry(r: Ranked) -> dict:
    """One ranked idea as the JSON output and the seal carry it. ``id`` is what a pick names:
    without it a caller rebuilt ids by position from the shortlist log (run b58e370627d1)."""
    return {"rank": r.rank, "id": r.scored.idea.id, "idea": r.scored.idea.text,
           "proposed_by": list(r.scored.idea.proposed_by),
           "combined": r.combined, "scores": dict(r.scored.scores), "unscored": list(r.scored.unscored()),
           "skipped": dict(r.scored.skipped)}


def _converge_note(scored: Sequence[Scored]) -> str:
    """Empty when at least one criterion was scored; otherwise why nothing was,
    as the distinct reasons CONVERGE recorded, so a run that scored nothing
    says so instead of ranking by divergence order (GRIP#8824)."""
    if any(s.scores for s in scored):
        return ""
    reasons = sorted({reason for s in scored for reason in s.skipped.values()}) or ["no reason recorded"]
    return "CONVERGE scored nothing (" + "; ".join(reasons) + "): the shortlist is not recorded"


def seal_ranking(topic: str, ranked: Sequence[Ranked], weights: Dict[str, float],
                 weights_source: str, *, idr_path: Optional[Path] = None) -> dict:
    """Seal the ranked idea set as a signed IDR leaf -- the one signer every
    GRIP decision uses (lib.precog.idr.build_idr + append_idr: both
    lib/prove_it_provenance.py and lib/sovereign_council.py._seal_idr build on
    exactly this pair rather than each writing its own signer). Never raises:
    a signing failure is reported, not hidden, and the ranking itself is
    unaffected either way -- sealing is provenance, not a gate.
    """
    decision = {"action": "ultraideate-ranking", "criteria": list(CRITERIA), "weights": weights,
               "weights_source": weights_source, "ranked": [_ranked_entry(r) for r in ranked]}
    return _seal_leaf(topic, "ultraideate-ranking", decision, {"idea_count": len(ranked)}, idr_path)


def seal_picks(topic: str, run_id: str, picked_ids: Sequence[str], k: int, *,
               idr_path: Optional[Path] = None) -> dict:
    """Seal the operator's pick from shortlist ``run_id`` as a signed IDR leaf, with the signer seal_ranking
    uses. The picks log is plain JSONL that anyone can edit, so a later ATTACK or DECIDE step could not prove
    which choice started it (Codex on GRIP#9364). The leaf names the run id, so the shortlist it answers can be
    found. Never raises, like seal_ranking: a failure is reported, and the recorded pick stands either way."""
    decision = {"action": "ultraideate-picks", "run_id": run_id, "picked_ids": list(picked_ids), "k": int(k)}
    return _seal_leaf(topic, "ultraideate-picks", decision, {"run_id": run_id, "picked_count": len(picked_ids)},
                      idr_path)


def _seal_leaf(topic: str, kind: str, decision: dict, inputs: dict, idr_path: Optional[Path]) -> dict:
    """Append one signed IDR leaf for an ultraideate record, and report the outcome rather than raise."""
    from lib.precog.idr import build_idr, append_idr
    try:
        idr = build_idr(prompt=topic, fingerprint="ultraideate", decision=decision,
                        predecessor_idr=None, depth=0, kind=kind, inputs=inputs)
        append_idr(idr, path=idr_path)
    except Exception as exc:  # noqa: BLE001 -- sealing is advisory, never fatal to what it seals
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "idr_id": idr.id, "custody": idr.audit.get("custody", "")}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _load_ideas_file(path: Path, *, prefix: str = "idea") -> List[Idea]:
    """Ideas from a JSON file, numbered ``<prefix>-1`` upward in file order."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data["ideas"] if isinstance(data, dict) else data
    ideas = []
    source = (f"file:{Path(path).name}",)  # an idea of unstated origin is the file's, never claimed as the operator's
    for i, item in enumerate(items):
        if isinstance(item, str):
            text, proposed_by = item, source
        else:
            text, proposed_by = str(item["text"]), tuple(item.get("proposed_by") or source)
        ideas.append(Idea(f"{prefix}-{i + 1}", text, proposed_by))
    return ideas


def _score_part(scored: Scored, criterion: str) -> str:
    if criterion in scored.scores:
        return f"{criterion}={scored.scores[criterion]:.2f}"
    return f"{criterion}=unscored({scored.skipped.get(criterion, '?')})"


def _print_ranked(ranked: Sequence[Ranked], weights: Dict[str, float], weights_source: str, note: str,
                  seed_ids: frozenset = frozenset()) -> None:
    print(note)
    print(f"weights: {weights} ({weights_source})")
    for r in ranked:
        s = r.scored
        parts = [_score_part(s, c) for c in CRITERIA]
        seed = " (seed)" if s.idea.id in seed_ids else ""
        print(f"#{r.rank} [{r.combined:.2f}] {s.idea.text}")
        print(f"    machine reading, precision unmeasured -- {', '.join(parts)}")
        print(f"    id {s.idea.id}{seed}, proposed by: {', '.join(s.idea.proposed_by)}")


def _print_seal_outcome(outcome: dict) -> None:
    if not outcome.get("ok"):
        print(f"seal: FAILED -- {outcome.get('reason', 'unknown error')}")
        return
    from lib.precog.idr import CUSTODY_DEMO
    if outcome.get("custody") == CUSTODY_DEMO:
        print(f"seal: WARNING -- sealed with a demo shared key (not a real signature), idr {outcome['idr_id']}. "
             "Run under ~/.grip/venv/bin/python for a real seal.")
    else:
        print(f"seal: sealed idr {outcome['idr_id']} (custody: {outcome.get('custody', '?')})")


def _diverge_note(ideas: List[Idea], result: DivergeResult) -> str:
    if result.skipped and not ideas:
        return f"DIVERGE skipped: {result.skipped}"
    return f"DIVERGE: {len(ideas)} ideas from {result.speakers_ok}/{result.speakers_total} speakers"


def _ideas_for(args: argparse.Namespace, cwd: str) -> Tuple[List[Idea], str, frozenset]:
    """The ideas a rank run scores, the note that says where they came from, and the ids of the seeds
    among them: the file named by ``--ideas-file`` (DIVERGE skipped), else DIVERGE's own result with
    ``--seeds-file``'s seeds first. The seeds file is read before DIVERGE runs, so a file that cannot
    be read stops the run before any council call is spent."""
    if args.ideas_file:
        ideas = _load_ideas_file(Path(args.ideas_file))
        return ideas, f"loaded {len(ideas)} ideas from {args.ideas_file}; DIVERGE skipped", frozenset()
    seeds = _load_ideas_file(Path(args.seeds_file), prefix="seed") if args.seeds_file else []
    result = diverge(args.topic, cwd=cwd)
    council = list(result.ideas)
    note = _diverge_note(council, result)
    if not args.seeds_file:
        return council, note, frozenset()
    ideas = merge_seeds(seeds, council)
    kept = {idea.id for idea in ideas}
    seed_ids = frozenset(seed.id for seed in seeds if seed.id in kept)
    # Counted directly against the seed texts, so a council idea that merges with another council
    # idea is never reported as a seed's copy (round 2 of the council on GRIP#9364).
    seed_keys = {_normalise(seed.text) for seed in seeds} - {""}
    credited = sum(1 for idea in council if _normalise(idea.text) in seed_keys)
    return ideas, (f"{note}; seeds: {len(seed_ids)} from {args.seeds_file}, "
                   f"council copies credited to a seed: {credited}"), seed_ids


def _shown(ranked: Sequence[Ranked], top: int, seed_ids: frozenset, *, shuffle: bool = False,
          rng: Optional[object] = None) -> List[Ranked]:
    """The first ``top`` ranked ideas, then every seed ranked below them: the operator's own
    idea is always shown, whatever its rank.

    ``shuffle`` randomises the DISPLAY order of that head alone (never the always-shown seed
    tail, and never Jev's own rank, which ``ranked_ids`` keeps as the true order precision is
    measured against) -- so a round can separate Jev's ranking from the operator's tendency to
    pick whatever is shown first (GRIP#9471). ``rng`` defaults to the ``random`` module's own
    default instance; a test injects an object with a ``shuffle`` method instead, for a
    deterministic reorder."""
    import random
    head = list(ranked[:top])
    if shuffle:
        (rng or random).shuffle(head)
    return head + [r for r in ranked[top:] if r.scored.idea.id in seed_ids]


def _run_rank(args: argparse.Namespace) -> int:
    cwd = os.getcwd()
    ideas, note, seed_ids = _ideas_for(args, cwd)
    if not ideas:
        print(note)
        return 1
    scored = converge(ideas, args.topic, cwd=cwd)
    weights, weights_source = operator_weights()
    ranked = combine(scored, weights)
    note = "; ".join(part for part in (note, _converge_note(scored)) if part)
    run_id = record_shortlist(args.topic, ranked, shuffled=args.shuffle)
    shown_order = "shuffled" if args.shuffle else "ranked"
    shown = _shown(ranked, args.top, seed_ids, shuffle=args.shuffle)
    if args.json:
        entries = [dict(_ranked_entry(r), seed=r.scored.idea.id in seed_ids) for r in shown]
        print(json.dumps({"note": note, "run_id": run_id, "weights": weights, "weights_source": weights_source,
                         "shown_order": shown_order, "ranked": entries}, indent=2))
    else:
        _print_ranked(shown, weights, weights_source, note, seed_ids)
        if run_id:
            tag = " (shown shuffled, to separate Jev's rank from position bias)" if args.shuffle else ""
            print(f"run id {run_id}{tag} (when the operator has picked: "
                  f"python -m lib.ultraideate picks {run_id} <ids> --topic <the same topic>)")
    if args.seal:
        _print_seal_outcome(seal_ranking(args.topic, ranked, weights, weights_source))
    return 0


def _run_report(args: argparse.Namespace) -> int:
    log_path = Path(args.log_file) if args.log_file else None
    report = precision_at_k(log_path)
    if args.json:
        print(json.dumps(report, indent=2))
    elif report["rows"] == 0:
        print("no picks recorded yet")
    else:
        print(f"{report['rows']} picking session(s); mean precision@k = {report['mean_precision_at_k']:.2f}; "
             f"random baseline = {report['mean_random_baseline']:.2f}; margin = {report['margin']:.2f}")
        for mode in ("ranked", "shuffled"):
            group = report["by_shown_order"][mode]
            if group["rows"]:
                print(f"  shown {mode}: {group['rows']} session(s); precision@k = "
                     f"{group['mean_precision_at_k']:.2f}; baseline = {group['mean_random_baseline']:.2f}; "
                     f"margin = {group['margin']:.2f}")
    return 0


def _run_picks(args: argparse.Namespace) -> int:
    picked = list(dict.fromkeys(args.picked))  # one id picked twice is one pick
    why = record_picks_for_run(args.topic, args.run_id, picked, k=args.k)
    if why:
        print(f"picks: nothing recorded -- {why}")
        return 1
    print(f"picks: recorded {len(picked)} picked id(s) for run {args.run_id}, judged at k={args.k}")
    if args.seal:
        _print_seal_outcome(seal_picks(args.topic, args.run_id, picked, args.k))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="DIVERGE + CONVERGE + COMBINE: outside ideas, Jev-ranked, operator-picked")
    sub = ap.add_subparsers(dest="cmd", required=True)

    rank = sub.add_parser("rank", help="DIVERGE + CONVERGE + COMBINE for one topic")
    rank.add_argument("topic")
    source = rank.add_mutually_exclusive_group()
    source.add_argument("--ideas-file", help="rank an existing JSON list of ideas; skips DIVERGE")
    source.add_argument("--seeds-file", help=("the operator's own ideas, in the --ideas-file format: DIVERGE "
                                              "still runs, every idea is scored together, each seed keeps its "
                                              "proposed_by, and every seed is shown whatever its rank"))
    rank.add_argument("--top", type=int, default=10,
                      help="how many ranked ideas to show (default 10), plus any seed ranked below them")
    rank.add_argument("--json", action="store_true")
    rank.add_argument("--seal", action="store_true", help="seal the full ranked set as a signed IDR leaf")
    rank.add_argument("--shuffle", action="store_true",
                      help=("show the --top ideas in random order instead of Jev's rank, and tag the "
                            "shortlist (and, via picks, its pick) \"shown_order\": \"shuffled\" -- so "
                            "report can separate Jev's ranking from the operator picking whatever is "
                            "shown first (GRIP#9471); Jev's own rank is unchanged either way"))

    report = sub.add_parser("report", help="precision@k of Jev's ranking against operator picks")
    report.add_argument("--log-file", help="an alternate picks log (default: logs/ultraideate-picks.jsonl)")
    report.add_argument("--json", action="store_true")

    picks = sub.add_parser("picks", help="record the operator's pick against the full ranked order of a rank run")
    picks.add_argument("run_id", help="the run id that rank printed")
    picks.add_argument("picked", nargs="*", help="every idea id the operator picked; none is a valid answer")
    picks.add_argument("--topic", required=True, help="the topic exactly as it was ranked; checked against the run")
    picks.add_argument("--k", type=int, default=5, help="the top-k the pick is judged at (default 5, as H823 registered)")
    picks.add_argument("--seal", action="store_true", help="also seal the recorded pick as a signed IDR leaf")

    args = ap.parse_args(argv)
    return {"rank": _run_rank, "report": _run_report, "picks": _run_picks}[args.cmd](args)


if __name__ == "__main__":
    # The shebang is /usr/bin/env python3, which need not be an interpreter
    # that can dual-sign (--seal). Without this the tool still runs and still
    # seals -- it just writes a forgeable demo-sealed record when a real seal
    # was one interpreter away (lib/pq_reexec.py). Imported off THIS checkout,
    # never as `lib.pq_reexec`, per rules/never-system-python-for-grip.md.
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(_Path(__file__))

    sys.exit(main())
