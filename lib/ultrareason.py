#!/usr/bin/env python3
"""ultrareason — moat-grounded, cost-tiered reasoning (task #502, /ultrareason).

The non-duplicative core (vs research-council / broly / converge): answer a hard
reasoning question by (1) GROUNDING it in the moat — what GRIP has actually
verified (lib/recall) and been contradicted on (the RED chain) — then (2) running
a COST-TIERED escalation ladder on Pool-B ($0, off the weekly Anthropic limit):
a cheap single-expert pass first, escalating to the sovereign council ONLY if a
confidence criterion is unmet. The ladder is the lib/converge kernel; the experts
are the existing Pool-B primitives. Returns the answer WITH its provenance.

Design (loop-engineering + syscall-doctrine):
- The orchestration is PURE over four injected callables (ground / cheap / council
  / criterion), so tests exercise the ladder with fakes — no HAL, no network.
- The real defaults are thin, fail-open wrappers over lib/recall + Pool-B
  (broly_council._query_grip_infer for cheap; bin/grip-sovereign-council for the
  escalation). Any failure degrades to the best answer already in hand — reasoning
  is best-effort, never a hard error.

Falsifier (see skills/ultrareason/SKILL.md): worthless if moat-grounding never
changes an answer vs ungrounded, OR the ladder never saves cost vs
always-council, OR it is never used. Tracked by hypothesis on first live use.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.converge import converge  # noqa: E402

# Cheap-tier confidence proxy: a thin or hedging answer is "not confident" and
# triggers one escalation. This is a heuristic, not a semantic judge — the
# escalation is bounded by max_escalations regardless.
_UNCERTAIN = (
    "i don't know", "i do not know", "not sure", "unsure", "insufficient",
    "cannot determine", "can't determine", "unclear", "no idea", "uncertain",
)
_STOPWORDS = frozenset((
    "what", "which", "when", "where", "should", "would", "could", "does",
    "about", "there", "their", "these", "those", "have", "with", "from",
    "that", "this", "will", "than", "then", "into", "over", "under", "your",
))
_MIN_CONFIDENT_LEN = 40
# Sentinel returned by _default_cheap when the cheap tier ERRORS (not merely
# unsure). _default_criterion must escalate on it — never surface the error as an
# answer, and a cheap-tier OUTAGE must not be mistaken for design-inertness
# (AGORA PR #4067 concern #4).
_CHEAP_UNAVAILABLE = "[ultrareason cheap tier unavailable"
# Sentinel PREFIXED to the answer when the council subprocess times out. Before it, the
# timeout fell into the bare `except Exception` below and the PRIOR answer came back as if
# it were the council's verdict, so a caller could not tell "the council agreed" from "the
# council never answered" (GRIP#5217 item 2 — the same slowness-read-as-death shape as the
# workflow stall watchdog, GRIP#5136). The prior is still kept (fail-open), but marked, and
# ultrareason() reports it as `council_timed_out`.
_COUNCIL_TIMED_OUT = "[ultrareason council timed out"
# Grounded in lib/sovereign_council.py: speakers run in parallel (ThreadPoolExecutor, one
# thread per speaker) under per-speaker read timeouts of up to 90 s (_READ_TIMEOUT_S), and
# the judge leg runs after them — so a healthy council can legitimately exceed the 180 s
# this used to allow. 300 s clears that worst case with margin; slowness past it is
# surfaced by the sentinel above, never swallowed.
_COUNCIL_TIMEOUT_S = 300
# An answer that says its grounding did not cover the question is not an answer the
# ladder should stop on, however long and fluent it is. GRIP#9543: a cheap answer said
# "The moat fact is irrelevant to the question" and then invented every model it named,
# and the length-and-hedge check above passed it. The prompt asks for _NOT_GROUNDED at
# the start of the reply, and only a line that BEGINS with it counts: the sovereign
# council's banner quotes the prompt, so a bare substring would flag every council
# answer, and a grounded answer may say "not grounded" about a side point (Codex and
# AGORA on GRIP#9562). A wrapped banner can still start a line with the instruction's
# own words, so the marker never counts when ", say what is missing" follows it (AGORA
# on GRIP#9572). The admissions below are the words a model uses when it ignores the
# instruction. A bare "do not answer" is left out, because a grounded answer can say it
# of a side question (AGORA on GRIP#9572); "do not answer the question" stays, because
# losing that admission lets an invented answer through, the failure this guards
# against (AGORA on GRIP#9591).
_NOT_GROUNDED = "NOT GROUNDED"
_NOT_GROUNDED_LINE = re.compile(r"^[ \t*#>_`-]*NOT GROUNDED\b(?!, say what is missing)", re.M)
_UNGROUNDED = (
    "fact is irrelevant", "facts are irrelevant", "facts provided are irrelevant",
    "facts do not answer the question", "facts provided do not answer the question",
)
# The prompt shows at most this many facts, and _default_ground returns no more, so
# the counts the result and the card report are the facts the model actually saw.
_PROMPT_FACTS = 12
_INSTRUCTION = (
    "Answer the question below from the GRIP facts provided. If they do not answer it, "
    f"begin your reply with {_NOT_GROUNDED}, say what is missing, and do not invent "
    "names, numbers or models.")
# How many /recall hits ground a question. Titles only, never file bodies (see
# _recall_pairs), so the cost of a larger number is prompt length, not exposure.
_RECALL_K = 5


def _as_text(result: Any) -> str:
    """Coerce a Pool-B result (dict or str) into answer text."""
    if isinstance(result, dict):
        for key in ("text", "content", "answer", "response"):
            if result.get(key):
                return str(result[key])
        return ""
    return str(result or "")


def _key_subjects(question: str, limit: int = 5) -> list[str]:
    """Naive subject extraction: content words >=4 chars, de-stopworded, capped.

    A dependency-light heuristic — the grounding tolerates imperfect subjects
    (a miss just yields fewer facts), so a POS tagger is not warranted (YAGNI)."""
    seen: list[str] = []
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", question.lower()):
        if tok in _STOPWORDS or tok in seen:
            continue
        seen.append(tok)
        if len(seen) >= limit:
            break
    return seen or [question.strip()[:60]]


def _fact_line(fact: dict) -> str:
    """One grounding fact as a prompt line: certainty, kind, path, and title if known."""
    line = (f"- [{fact.get('status', '?')}] {fact.get('type', 'record')}: "
            f"{fact.get('path', '(no path)')}")
    title = fact.get("title")
    return f"{line} -- {title}" if title else line


def _build_prompt(question: str, context: dict, prior: Optional[str] = None) -> str:
    """Compose the reasoning prompt with the moat grounding injected."""
    lines = [_INSTRUCTION, "", f"QUESTION: {question}", ""]
    facts = context.get("facts", [])
    if facts:
        lines.append("MOAT FACTS (verified records, and /recall search hits marked FOUND):")
        lines += [_fact_line(f) for f in facts[:_PROMPT_FACTS]]
        if len(facts) > _PROMPT_FACTS:  # no silent caps
            lines.append(f"(and {len(facts) - _PROMPT_FACTS} more facts not shown)")
        lines.append("")
    contradictions = context.get("contradictions", [])
    if contradictions:
        lines.append("MOAT CONTRADICTIONS (claims GRIP was proven wrong on — do "
                     "NOT repeat them):")
        lines += [f"- {c}" for c in contradictions[:8]]
        lines.append("")
    if prior:
        lines += ["A cheaper first-pass answer (improve or correct it):", prior, ""]
    return "\n".join(lines)


# --------------------------------------------------------------- default deps

def _ground_subject(idx, subject: str, store) -> list[tuple[dict, dict]]:
    """(fact, provenance) pairs for one subject — single comprehension, no nesting."""
    try:
        res = idx.query_subject(subject, store=store)
    except Exception:
        return []
    return [(a, {"subject": subject, "path": a.get("path"),
                 "hash": a.get("blob_hash"), "idr": a.get("idr_id")})
            for a in (getattr(res, "artefacts", []) or [])]


def _moat_pairs(question: str, store) -> list[tuple[dict, dict]]:
    """(fact, provenance) pairs from the moat's verified records. Fail-open to []."""
    try:
        from lib.recall import index as _idx
    except Exception:
        return []
    pairs: list[tuple[dict, dict]] = []
    for subject in _key_subjects(question):
        pairs.extend(_ground_subject(_idx, subject, store))
    return pairs


def _recall_pair(hit: dict, snapshot) -> tuple[dict, dict]:
    """One /recall hit as a (fact, provenance) pair. A hit is a search match, never a
    verified record, so its status stays the cascade's certainty (FOUND)."""
    return ({"status": hit.get("certainty", "FOUND"), "type": hit.get("kind", "record"),
             "path": hit["id"], "title": hit.get("title")},
            {"subject": "recall", "path": hit["id"], "hash": None, "idr": None,
             "snapshot": snapshot})


def _recall_pairs(question: str, store=None) -> list[tuple[dict, dict]]:
    """(fact, provenance) pairs from /recall's cascade: the memory records, rules, docs
    and skills whose words match the question (GRIP#9543: the moat index alone found one
    unrelated record while the answer sat in a memory record and a measurement doc).

    Titles and paths only, never file bodies: the prompt goes to outside models, and a
    title is what the index already shows. Records marked private are never indexed.
    The caller's moat store goes to the cascade too, so its verified layer searches the
    same store as _moat_pairs (Codex on GRIP#9562). Each provenance entry keeps the
    cascade's snapshot, the digest of the store versions it read, so a reader can tell
    which index state ranked the hit. Fail-open to [] on any recall error or bad hit."""
    try:
        from lib.recall.cascade import recall
        res = recall(question, k=_RECALL_K, moat_store=store)
        snapshot = res.get("snapshot")
        return [_recall_pair(h, snapshot) for h in res.get("hits", [])
                if isinstance(h, dict) and h.get("id")]
    except Exception:
        return []


def _prefer_verified(held, fact: dict, prov: dict) -> tuple[dict, dict]:
    """The pair to keep when a moat record arrives for a path. A verified record
    outranks a search hit on the same path and keeps the hit's title, since moat
    records carry none (AGORA on GRIP#9562: dropping the record's hash was a
    provenance downgrade). An earlier moat record for the path stays."""
    if held and held[1].get("subject") != "recall":
        return held
    title = held[0].get("title") if held else None
    return ({**fact, "title": title} if title else fact, prov)


def _default_ground(question: str, store) -> dict:
    """Ground the question in /recall's hits and the moat, one fact per path. The
    recall hits come first: they are ranked against the whole question, while the
    moat matches single words and can return many unrelated records, and the prompt
    shows only the first _PROMPT_FACTS, so no more are returned. A moat record replaces
    a hit in place. A record with no path is keyed by its hash or IDR id, so distinct
    ones stay apart (AGORA on GRIP#9572) and the same one found under several subjects
    is listed once (AGORA on GRIP#9591)."""
    merged: dict = {}
    for fact, prov in _recall_pairs(question, store):
        merged.setdefault(fact.get("path"), (fact, prov))
    for fact, prov in _moat_pairs(question, store):
        key = fact.get("path") or fact.get("blob_hash") or fact.get("idr_id") or object()
        merged[key] = _prefer_verified(merged.get(key), fact, prov)
    pairs = list(merged.values())[:_PROMPT_FACTS]
    return {"facts": [p[0] for p in pairs], "contradictions": [],
            "provenance": [p[1] for p in pairs]}


def _default_cheap(question: str, context: dict) -> str:
    """Cheap Pool-B single pass (HAL grip_infer, econ model). Fail-open."""
    prompt = _build_prompt(question, context)
    try:
        from lib.hal_llm_adapter import _query_grip_infer, _resolve_econ_default
        return _as_text(_query_grip_infer(prompt, model=_resolve_econ_default(),
                                          audit=False))
    except Exception as exc:  # HAL offline / import failure -> escalate (not return)
        return f"{_CHEAP_UNAVAILABLE}: {exc}]"


def _default_council(question: str, context: dict, prior: Optional[str]) -> str:
    """Escalate to the sovereign council (Pool-B, non-Anthropic, sealed).

    Fail-open: on any error keep the prior (cheap) answer — never crash the
    reasoning because the heavier tier is unreachable."""
    prompt = _build_prompt(question, context, prior=prior)
    try:
        # run_text, not subprocess.run(text=True): the council's verdict is
        # prose and comes back as this function's ANSWER, so a cp1252 read on
        # Windows would hand the caller mojibake with returncode 0.
        from lib._compat import run_text
        proc = run_text(
            [str(_ROOT / "bin" / "grip-sovereign-council"), prompt],
            capture_output=True, timeout=_COUNCIL_TIMEOUT_S, cwd=str(_ROOT),
            check=False)
        out = (proc.stdout or "").strip()
        return out or (prior or "")
    except subprocess.TimeoutExpired:
        marker = f"{_COUNCIL_TIMED_OUT} after {_COUNCIL_TIMEOUT_S}s; prior answer kept]"
        return f"{marker}\n{prior}" if prior else marker
    except Exception:
        return prior or ""


def _says_ungrounded(answer: str) -> bool:
    """True when a line of the answer begins with NOT GROUNDED, or the answer says its
    facts are irrelevant or do not answer the question (GRIP#9543)."""
    text = str(answer or "")
    if _NOT_GROUNDED_LINE.search(text):
        return True
    low = text.lower()
    return any(marker in low for marker in _UNGROUNDED)


def _is_grounded(context: dict, answer: str) -> bool:
    """Grounded means there were facts AND the answer did not say they missed the
    question. A caller must not present an ungrounded answer as GRIP's knowledge."""
    return bool(context.get("facts")) and not _says_ungrounded(answer)


def _default_criterion(answer: str, context: dict) -> bool:
    """Cheap confidence proxy: a substantive, non-hedging, grounded answer stops the
    ladder; a thin, uncertain, ungrounded or cheap-tier-ERROR answer escalates. A
    cheap-tier outage (the _CHEAP_UNAVAILABLE sentinel) escalates and is never
    surfaced as the answer — an outage is not a design justification (AGORA PR
    #4067 #4)."""
    text = str(answer or "").strip()
    if _CHEAP_UNAVAILABLE in text:
        return False  # cheap tier crashed -> escalate, do not return the error
    if _says_ungrounded(text):
        # With facts to hand, the council may find what the cheap tier missed. With
        # none, it has nothing more to go on, so stop and let `grounded` say so
        # (AGORA on GRIP#9562: every miss would otherwise pay for a council).
        return not (context or {}).get("facts")
    low = text.lower()
    if len(low) < _MIN_CONFIDENT_LEN:
        return False
    return not any(marker in low for marker in _UNCERTAIN)


# ------------------------------------------------------------------- kernel

def ultrareason(question: str, *, store=None, max_escalations: int = 1,
                ground: Optional[Callable] = None,
                cheap: Optional[Callable] = None,
                council: Optional[Callable] = None,
                criterion: Optional[Callable] = None,
                label: str = "ultrareason") -> dict:
    """Moat-grounded, cost-tiered reasoning over the lib/converge kernel.

    ground/cheap/council/criterion are injectable (DIP) — the defaults wire the
    real Pool-B + recall primitives; tests pass fakes. Returns the answer plus
    provenance (which moat facts grounded it, which tier answered, whether it
    escalated)."""
    ground = ground or _default_ground
    cheap = cheap or _default_cheap
    council = council or _default_council
    criterion = criterion or _default_criterion

    context = ground(question, store)
    initial = {"question": question, "context": context, "tier": "cheap",
               "answer": _as_text(cheap(question, context))}

    def _crit(state: dict) -> bool:
        return bool(criterion(state["answer"], state["context"]))

    def _improve(state: dict) -> dict:
        escalated = _as_text(council(state["question"], state["context"],
                                     state["answer"]))
        return {**state, "tier": "council", "answer": escalated}

    result = converge(initial, _crit, _improve, max_depth=max_escalations,
                      label=label)
    final = result.best_state
    timed_out = str(final["answer"]).startswith(_COUNCIL_TIMED_OUT)
    return {
        "question": question,
        "answer": final["answer"],
        # A timed-out escalation keeps the CHEAP tier's answer, so `tier` names who
        # actually answered, never who was asked; `escalated` records that the council
        # was tried (Codex review on PR #5255, P2: a header saying "sovereign council"
        # over a fallback answer lets a parser treat the fallback as a verdict).
        "tier": "cheap" if timed_out else final["tier"],
        "escalated": result.iterations > 0,
        "council_timed_out": timed_out,
        "grounded": _is_grounded(context, final["answer"]),
        "satisfied": result.satisfied,
        "iterations": result.iterations,
        "grounded_on": context.get("provenance", []),
        "moat_facts": len(context.get("facts", [])),
        "contradictions": len(context.get("contradictions", [])),
    }


# ----------------------------------------------------- provenance card (delight)
# Restored 2026-09-02. Shipped in f90aecac (PR #4086) and silently dropped by
# 8d4406b1, a combined council-prompt-fix + sensitive-migration commit that
# overwrote this module from a stale tree and never mentions the card; the three
# tests and skills/ultrareason/SKILL.md kept importing it, so that test file has
# been partly red since 2026-07-18 (rules/shared-tree-is-not-canonical.md class).

_COUNCIL_SEAL_RE = re.compile(r"PROVENANCE\s+sha256\s*:\s*([0-9a-f]{16,64})", re.I)


def _tier_label(result: dict, timed_out: bool) -> str:
    """Who answered, as the card's header names it. A kept prior is never labelled a
    council answer, whatever `tier` says: the header is what a parser reads first
    (Codex review on PR #5255, P2)."""
    if timed_out:
        return "cheap Pool-B expert (single pass); the council timed out, so this is the kept prior"
    if result.get("tier", "?") == "council":
        return "sovereign council — non-Anthropic panel + exogenous judge"
    return "cheap Pool-B expert (single pass)"


def _ground_line(result: dict, facts: list) -> str:
    """How the answer is grounded, with verified records and /recall search hits
    counted apart: a hit is a match, not a verified fact (Codex on GRIP#9562)."""
    n_facts = result.get("moat_facts", len(facts))
    if not n_facts:
        if result.get("grounded") is False:
            return "no facts found -- NOT GROUNDED, nothing from GRIP's records backs this answer"
        return "no moat match — reasoned from first principles"
    n_hits = sum(1 for p in facts if (p or {}).get("subject") == "recall")
    hits = f" and {n_hits} /recall search hit(s) (found, not verified)" if n_hits else ""
    return f"{n_facts - n_hits} verified moat fact(s){hits} — reasoned from GRIP's own knowledge"


def _grounding_lines(result: dict) -> list:
    """The moat-grounding line, up to six facts, and a count of any left out."""
    facts = result.get("grounded_on") or []
    n_facts = result.get("moat_facts", len(facts))
    lines = [f"│ moat-grounded: {_ground_line(result, facts)}"]
    for pair in facts[:6]:
        path = (pair or {}).get("path") or "(no path)"
        subj = (pair or {}).get("subject") or ""
        lines.append(f"│   • {path}" + (f"  ← {subj}" if subj else ""))
    if len(facts) > 6:
        # A cap that says nothing reads as "that was everything" (no silent caps).
        lines.append(f"│   … and {len(facts) - 6} more moat fact(s) not shown")
    if n_facts and result.get("grounded") is False:
        # The facts were there, and the answer says they missed the question (GRIP#9543).
        lines.append("│ grounding    : NOT GROUNDED -- the answer says its facts did not cover the question")
    return lines


def _council_lines(result: dict, timed_out: bool) -> list:
    """The council's outcome: a timeout, a seal with its re-verify command, or a plain escalation."""
    if timed_out:
        # The answer below the marker is the CHEAP tier's, kept fail-open. Saying so
        # here is the whole point of surfacing the timeout (GRIP#5217 item 2).
        return [f"│ council      : TIMED OUT after {_COUNCIL_TIMEOUT_S}s — prior answer shown, NOT a council verdict"]
    if not result.get("escalated"):
        return []
    seal = _COUNCIL_SEAL_RE.search(str(result.get("answer") or ""))
    if not seal:
        return ["│ escalated    : to the sovereign council (Pool-B)"]
    short = seal.group(1)[:16]
    return [f"│ sealed       : sha256 {short} (zero-Anthropic)",
            f"│ re-verify    : grip-sovereign-council --verify {short}"]


def render_provenance_card(result: dict) -> str:
    """Render a compact provenance card for an ``ultrareason`` result: which moat
    facts grounded the answer, which tier answered, and — if it escalated to the
    sovereign council — the council's sha256 seal + re-verify command. Pure and
    fail-open: safe on a partial/failed result (every field defaults).

    The delight surface of the provability moat — every reasoned answer shows WHY
    it can be trusted (grounded in verified facts; an escalated answer carries a
    tamper-evident, independently re-verifiable seal). Operator-facing; the
    moat-fact paths are internal and not for external publication.
    """
    timed_out = bool(result.get("council_timed_out"))
    lines = ["┌─ /ultrareason · provenance ───────────────────────────",
             f"│ answered by  : {_tier_label(result, timed_out)}",
             *_grounding_lines(result),
             *_council_lines(result, timed_out),
             "└────────────────────────────────────────────────────────"]
    return "\n".join(lines)
