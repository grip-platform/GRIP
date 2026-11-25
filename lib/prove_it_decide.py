"""Jev first on quote support, the council for the rest (the Jev plan's Phase 3.3).

For each citation the deterministic floor located (verified, or a whitespace
variant), one decision-model call (TypeSafe's Jev, through HAL decide) asks a
typed Choice: does the quote, read in its source paragraph exactly as the
council reads it, support the claim, contradict it, or say nothing about it? A
reading whose top probability is at least REVIEW_BELOW (0.80, the line the
vendor's own citation-check recipe draws) sets the citation's support.
Anything less sure goes to the existing multi-model council
(lib/prove_it_council.py): Jev screens, the council decides, as in V>>'s D-5.

Jev leans towards whichever option is listed first, and "supports" is the
direction in which a wrong answer does harm. So the one call asks the Choice
twice, its options in opposite orders, and averages each verdict's two
probabilities. A reading needs BOTH answers complete: one order alone carries
exactly the first-listed bias the pair exists to cancel, so with only one complete
answer there is no reading and the citation goes to the council (Codex, GRIP#7991).

A confident reading also becomes a support attestation in the shape GRASP
verifies (grasp/prove_it.py SupportAttestation, GRASP#22): provider, model, a
hash of the question configuration, verdict, probabilities, calibrated=False,
machine_reading=True and precision_ref. precision_ref stays "unmeasured" until
the Phase 3.4 probe and calibration loop measure this question's precision.

Opt-in: GRIP_PROVEIT_DECIDE=1. The Phase 3.4 settling probe passed on
2026-10-11 (H809, docs/measurements/2026-10-11-h809-support-probe.md), which
permits internal use, labelled "precision unmeasured". It stays opt-in because
prove_it.py render also makes outward and legal artefacts and cannot tell them
from internal ones. Set it for runs whose artefact stays internal.
The separate per-render public-legal scope permits outward machine review notes
only for allowlisted measured sources after a document-wide privacy preflight. Off, the council reviews every citation
exactly as before. The test suite's fence for every decision-model call
(GRIP_CLASSIFY_NO_DECIDE=1) also turns it off.

Data limit: internal and public sources only. The decision-model privacy floor
(lib/decision_privacy.may_send) runs on the claim and the quote together:
nothing from the guardian repository, nothing the redaction shield would change.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

#: The verdicts, and what each means to the model.
OPTIONS = {
    "supports": "the quote states what the claim asserts",
    "contradicts": "the quote states the opposite of the claim, or something incompatible with it",
    "says_nothing": "the quote does not address what the claim asserts",
}
#: GRIP's support vocabulary for each confident verdict (lib/prove_it.py Citation.support).
SUPPORT_OF = {"supports": "supported", "contradicts": "unsupported", "says_nothing": "unsupported"}
REVIEW_BELOW = 0.80
_QUESTION = "Does the quoted text support the claim?"
_ON = ("GRIP_PROVEIT_DECIDE", "1")
_FENCE = ("GRIP_CLASSIFY_NO_DECIDE", "1")

Decide = Callable[[str, dict], Optional[dict]]


def _choice(order: Sequence[str]) -> dict:
    return {"type": "choice", "instructions": _QUESTION, "criteria": {v: OPTIONS[v] for v in order}}


#: The one question configuration: both orders. Its hash names it in every attestation,
#: so a later precision measurement can be tied to exactly this wording and these options.
QUESTIONS = {"forward": _choice(list(OPTIONS)), "reversed": _choice(list(OPTIONS)[::-1])}


def question_digest(questions: dict) -> str:
    """SHA-256 of the questions with their option ORDER kept: each question's criteria
    as a sequence of [option, meaning] pairs, in the order the model sees them. A
    sorted-keys JSON dump would erase exactly the order this scheme exists to balance,
    so the forward and reversed questions would hash alike (Codex, GRIP#7991)."""
    ordered = [[name, q.get("type", ""), q.get("instructions", ""), list(map(list, q.get("criteria", {}).items()))]
               for name, q in questions.items()]
    return hashlib.sha256(json.dumps(ordered).encode("utf-8")).hexdigest()


QUESTION_SHA256 = question_digest(QUESTIONS)
#: How far the three probabilities of one answer may sum from 1 and still be a distribution
#: (a model that rounds to two decimals can give 0.99 or 1.01).
DISTRIBUTION_TOLERANCE = 0.02


@dataclass(frozen=True)
class Reading:
    """Jev's reading of one claim and quote, or why there is none."""
    verdict: str = ""
    probabilities: Dict[str, float] = field(default_factory=dict)
    model: str = ""
    skipped: str = ""

    @property
    def confident(self) -> bool:
        return bool(self.verdict) and self.probabilities.get(self.verdict, 0.0) >= REVIEW_BELOW

    def attestation(self) -> dict:
        """The reading as GRASP's SupportAttestation fields (GRASP#22)."""
        return {"provider": "typesafe", "model": self.model, "question_sha256": QUESTION_SHA256,
                "verdict": self.verdict, "probabilities": dict(self.probabilities),
                "calibrated": False, "machine_reading": True, "precision_ref": "unmeasured"}


def enabled() -> bool:
    return os.environ.get(_ON[0], "") == _ON[1] and os.environ.get(_FENCE[0], "") != _FENCE[1]


def _complete(answer) -> Dict[str, float]:
    """One question's probability for every verdict, or {} when any is missing or malformed,
    or when the three do not sum to 1 within DISTRIBUTION_TOLERANCE: three values of 0.9
    are each in range and are not a distribution, and would otherwise settle a citation on
    whichever option comes first (Codex, GRIP#7991)."""
    probs = answer.get("probabilities") if isinstance(answer, dict) else None
    probs = probs if isinstance(probs, dict) else {}
    valid = {v: float(probs[v]) for v in OPTIONS
             if isinstance(probs.get(v), (int, float)) and not isinstance(probs.get(v), bool)
             and 0 <= probs[v] <= 1}
    if len(valid) != len(OPTIONS) or abs(sum(valid.values()) - 1.0) > DISTRIBUTION_TOLERANCE:
        return {}
    return valid


def _averaged(answers: dict) -> tuple:
    """The two orders' mean probability per verdict, and "" -- or ({}, why) when either
    answer is incomplete. One complete answer is never enough: it is a single option
    order, carrying the first-listed lean the two orders exist to cancel, so it must not
    settle a citation or produce a two-question attestation (Codex, GRIP#7991)."""
    readings = [_complete(answers.get(q)) for q in QUESTIONS]
    if all(readings):
        return {v: sum(r[v] for r in readings) / len(readings) for v in OPTIONS}, ""
    return {}, "incomplete_answer" if any(readings) else "no_answer"


def _default_decide(state: str, questions: dict) -> Optional[dict]:
    from hal_llm_adapter import _query_grip_decide
    return _query_grip_decide(state, questions, timeout_s=10.0)


def _floor(text: str, cwd: Optional[str]) -> tuple:
    try:
        from decision_privacy import may_send
        return may_send(text, cwd=cwd)
    except Exception:  # noqa: BLE001 -- no floor, no send
        return False, "privacy_floor_unavailable"


def support_reading(claim: str, quote: str, *, cwd: Optional[str] = None,
                    decide: Decide = _default_decide) -> Reading:
    """Jev's order-balanced reading of whether ``quote`` supports ``claim``.

    ``quote`` is whatever text the reading should judge: review_citations passes the
    quote in its source paragraph, as the council does, so that a fragment such as
    "the drug is safe" is read inside "The assertion that the drug is safe is false"."""
    state = f"CLAIM: {claim}\nQUOTE: {quote}"
    allowed, why = _floor(state, cwd)
    if not allowed:
        return Reading(skipped=f"privacy_floor:{why}")
    try:
        result = decide(state, QUESTIONS)
    except Exception:  # noqa: BLE001 -- advisory; the council still answers
        result = None
    answered = isinstance(result, dict) and result.get("ok") and isinstance(result.get("answers"), dict)
    probs, why = _averaged(result["answers"]) if answered else ({}, "no_answer")
    # The model that answered is kept even when its answer cannot be used, so a probe
    # report still names every model that shaped its verdict (Codex, GRIP#8000).
    model = str(result.get("model") or "") if isinstance(result, dict) and result.get("ok") else ""
    if not probs:
        return Reading(model=model, skipped=why)
    verdict = max(OPTIONS, key=lambda v: probs[v])
    return Reading(verdict, probs, model)


def _settle(citation, reading: Reading) -> None:
    top = reading.probabilities[reading.verdict]
    citation.support = SUPPORT_OF[reading.verdict]
    citation.support_detail = (f"{reading.model or 'Jev'} machine reading: {reading.verdict.replace('_', ' ')} "
                               f"{top:.2f}, precision unmeasured")
    citation.support_attestation = reading.attestation()


def _council(cites, sources) -> None:
    import prove_it_council
    prove_it_council.review_citations(cites, sources)


def _window(c, by_id: dict) -> str:
    """The quote in its source context: the same paragraph window the council judges
    (prove_it_council._extract_window), never the bare quote, which can read as
    supporting a claim its surrounding sentence denies (Codex, GRIP#7991)."""
    from prove_it_council import _extract_window
    src = by_id.get(c.source_id)
    return (_extract_window(src.text, c.start, c.end, 1) if src else "") or c.quote


def review_citations(cites: list, sources: list, *, decide: Decide = _default_decide,
                     council: Callable = _council, cwd: Optional[str] = None) -> list:
    """Jev settles the citations it is sure of; the council reviews the rest as before.

    Only a citation the floor located is asked: the council marks a quote that was
    not found n/a, and no reading can rescue it (the floor stays sovereign). What is
    asked about is the quote in its source paragraph, exactly the window the council
    would judge, so the two paths read the same text."""
    if not enabled():
        council(cites, sources)
        return cites
    from prove_it import STATUS_FUZZY, STATUS_VERIFIED
    by_id = {s.id: s for s in sources}
    settled = set()
    for c in cites:
        if c.status not in (STATUS_VERIFIED, STATUS_FUZZY):
            continue
        reading = support_reading(c.claim, _window(c, by_id), cwd=cwd, decide=decide)
        if reading.confident:
            _settle(c, reading)
            settled.add(id(c))
    rest = [c for c in cites if id(c) not in settled]
    if rest:
        council(rest, sources)
    return cites


PUBLIC_LEGAL_SOURCES = _LIB.parent / "config" / "public-legal-sources.json"
PUBLIC_LEGAL_LABEL = "machine reading -- precision measured on public legal text (H815)"


def _public_legal_gate(spec: dict, cites: list, sources: list) -> str:
    """Exact measured URLs only; preflight the entire document before any send."""
    if os.environ.get(_FENCE[0], "") == _FENCE[1]:
        return "decision_model_fenced"
    try:
        urls = json.loads(PUBLIC_LEGAL_SOURCES.read_text(encoding="utf-8"))["urls"]
        if not isinstance(urls, list) or not urls or not all(isinstance(u, str) for u in urls):
            return "allowlist_unavailable"
        allowed_urls = set(urls)
    except (OSError, ValueError, KeyError, TypeError):
        return "allowlist_unavailable"
    raw_sources = spec["sources"]
    by_id = {s["id"]: s for s in raw_sources}
    if len(by_id) != len(raw_sources):
        return "ambiguous_source_id"
    if any(by_id.get(c.source_id, {}).get("url", "") not in allowed_urls for c in cites):
        return "source_not_allowlisted"
    # Include uncited prose too: a public citation does not make a client document public.
    text = "\n".join([spec.get("title", ""), spec["response"],
                      *(s.text for s in sources), *(c.claim for c in cites),
                      *(c.quote for c in cites)])
    allowed, why = _floor(text, None)
    return "" if allowed else f"privacy_floor:{why}"


def public_legal_readings(spec: dict, cites: list, sources: list) -> str:
    """Per-render advisory notes; never fall back to a council or change L1.

    Choosing this scope asserts that claims as well as quotes are public legal
    text. URL membership constrains sources; the privacy floor remains mandatory.
    Stage every reading so a later privacy refusal suppresses the entire batch.
    """
    why = _public_legal_gate(spec, cites, sources)
    if why:
        return why
    from prove_it import STATUS_FUZZY, STATUS_VERIFIED
    by_id = {s.id: s for s in sources}
    pending = []
    for c in cites:
        if c.status not in (STATUS_VERIFIED, STATUS_FUZZY):
            continue
        reading = support_reading(c.claim, _window(c, by_id), decide=_default_decide)
        if reading.skipped:
            return reading.skipped
        pending.append((c, reading))
    for c, reading in pending:
        c.support = "review"
        c.support_detail = (f"{PUBLIC_LEGAL_LABEL}: {reading.verdict.replace('_', ' ')} "
                            f"{reading.probabilities[reading.verdict]:.2f}")
        c.support_attestation = {**reading.attestation(), "precision_ref": "H815"}
    return ""
