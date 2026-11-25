#!/usr/bin/env python3
"""ultraread.py — Multi-model comprehension council orchestrator.

Usage:
    bin/grip-py -m lib.ultraread path/to/file.py
    bin/grip-py -m lib.ultraread --models deepseek/deepseek-v4-pro,xai/grok-4.6 path/to/spec.md

Short labels (``opus``, ``gpt-oss-120b``, ...) resolve to a HAL
``provider/model`` address through ``lib/council_seat.py``'s
``model_address``; an explicit ``provider/model`` address passes through
verbatim.

Writes deliverable to state/ultraread/<slug>-<timestamp>.md and prints the path.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import math
import os
import re
import sys
import time
from collections import Counter
from itertools import chain
from pathlib import Path
from typing import Callable, Optional

try:  # the three-tier import fallback lib/ultrajudge.py already uses
    from lib.council_seat import canonical_label, json_object, model_address
except ImportError:  # pragma: no cover - script-mode
    try:
        from council_seat import canonical_label, json_object, model_address
    except ImportError:
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P(__file__).resolve().parent))
        from council_seat import canonical_label, json_object, model_address

CLAUDE_DIR = Path(__file__).parent.parent
STATE_DIR = CLAUDE_DIR / "state" / "ultraread"
# Pool B seats, none Anthropic (a default /ultraread must not spend the weekly
# Claude limit: rules/fan-out-spends-the-weekly-limit.md point 4) and none
# OpenAI (rules/gpt-on-the-subscription-never-credits.md), each observed
# serving through the HAL gateway on 2026-11-12. Before this fix the old
# defaults opus/sonnet never reached Anthropic at all — _dispatch_real called
# adapter.generate(), a method with no model parameter, so every seat
# answered down HAL's shared default route regardless of the label it was
# given (GRIP#9187) — routing them now would start spending the weekly limit
# on every default run.
DEFAULT_MODELS = ["deepseek/deepseek-v4-pro", "xai/grok-4.6",
                   "gemini/gemini-3.1-pro-preview", "qwen/qwen3.7-plus"]
PROMPT_CHARS = 8000  # each model reads this many characters of the target
# The bound /ultrathink gives a HAL seat (lib/ultrathink_trio.py
# _TIMEOUT_S["hal"]) — a reading seat sending an 8,000-character prompt can
# need longer than the ladder's 45-second default to answer.
SEAT_TIMEOUT_S = 240.0

_ULTRAREAD_PROMPT = """You are a code/spec comprehension reviewer. Analyse the target named "{target}".

Return ONLY a JSON object (no prose before or after) matching this exact schema:
{{
  "structural_map": "<one-line summary naming the top-level constructs (modules, classes, functions) actually present>",
  "key_claims": [
    {{"claim": "<specific factual claim citing real identifiers from the target>", "lines": [<start_line_int>, <end_line_int>]}}
  ],
  "risks": ["<specific risk citing a real construct>"],
  "gaps": ["<specific missing test/handler/invariant>"],
  "questions": ["<specific clarifying question about a real construct>"],
  "confidence": <float between 0.0 and 1.0>
}}

Rules: name actual functions/classes/files from the target. Do NOT use generic placeholders like "the main module" or "core logic". If you cannot identify specifics, return confidence < 0.3.

You have no tools and cannot read more of the target than the text below. Answer from that text alone, in this one reply; do not announce any further step. If the text is cut off, say so in "gaps".

Target content (first {limit} chars):
---
{content}
---"""


class UltraReadRefused(RuntimeError):
    """Raised when the live path produced no live reading from any seat.

    Mirrors lib/ultrajudge.py's HALUnavailableError: V>>'s standing position
    (ULTRAPROMPT-hal-wiring-perimeter §1) is that an all-stub deliverable
    wearing a council's clothes is worse than no deliverable — no quorum is
    not a verdict (rules/exogenous-anchor-law.md).
    """


def _dispatch_stub(model: str, target: str, content: str, reason: str = "offline",
                   address: Optional[str] = None) -> dict:
    """Deterministic stub — used as fallback when HAL chain is exhausted or
    when ULTRAREAD_USE_HAL=0 (e.g. in hermetic tests). Returns canned per-model
    response. Kept as fallback (not removed) per syscall-doctrine fail-open
    contract — a stub is strictly safer than crashing the council.

    ``reason`` names why this seat is a stub ("offline", "not served", "live
    dispatch failed", ...) so the deliverable's "Seats without a live
    reading" section can say why without a reader cross-referencing stderr.

    ``address`` is the RESOLVED address this seat was actually sent to, when
    one was resolved before the stub fallback fired (the "not served" and
    "live dispatch failed" branches of ``_dispatch_real`` know it; the
    "adapter not importable" branch never got far enough to resolve one, so
    it is left out there and the renderer falls back to "address unresolved"
    — GRIP#9187 review: every stub used to render "address unresolved" even
    when the address was known and had just been warned about on stderr).
    """
    stub = {
        "model": model,
        "structural_map": f"[{model}-stub] Top-level module with primary entry point.",
        "key_claims": [
            {"claim": f"[{model}-stub] Core logic is well-encapsulated.", "lines": [1, 20]},
            {"claim": f"[{model}-stub] Error handling is explicit.", "lines": [30, 45]},
        ],
        "risks": [f"[{model}-stub] Stub: no live analysis performed."],
        "gaps": [f"[{model}-stub] Stub: integration tests absent."],
        "questions": [f"[{model}-stub] Stub: is this target current?"],
        "confidence": 0.5,
        "source": "stub",
        "reason": reason,
    }
    if address is not None:
        stub["address"] = address
    return stub


def _warn(message: str) -> None:
    """One line on stderr, so a stub fallback or a cut target is never silent (GRIP#9159)."""
    print(f"[ultraread] {message}", file=sys.stderr)


def _adapter_class():
    """Import HALLLMAdapter both as ``-m lib.ultraread`` and as a file run directly.

    Run as a file there is no parent package, so the relative import raises
    ImportError; that used to turn every seat into a stub without a word.
    """
    try:
        from .hal_llm_adapter import HALLLMAdapter  # type: ignore
    except ImportError:
        root = str(CLAUDE_DIR.resolve())
        if root not in sys.path:
            sys.path.insert(0, root)
        from lib.hal_llm_adapter import HALLLMAdapter  # type: ignore
    return HALLLMAdapter


def _resolve_adapter_factory(model: str, adapter_factory: Optional[Callable]) -> Optional[Callable]:
    """The caller-supplied factory, or the default HAL one. Returns None
    (after warning) when the default adapter cannot be imported at all —
    the caller then stubs this seat."""
    if adapter_factory is not None:
        return adapter_factory
    try:
        adapter_class = _adapter_class()
    except ImportError as exc:
        _warn(f"{model}: HAL adapter not importable ({exc}); using the stub")
        return None
    return lambda: adapter_class().connect()


def _call_seat(adapter, address: str, target: str, content: str):
    """One real provider call for one seat. Returns ``(text, result)``; text
    is ``None`` when the pin was not served (empty reply) — the caller
    decides the stub reason. Any adapter exception propagates."""
    prompt = _ULTRAREAD_PROMPT.format(target=target, content=content[:PROMPT_CHARS],
                                      limit=PROMPT_CHARS)
    result = adapter.infer(prompt, model=address, audit=False, no_failover=True,
                           timeout_s=SEAT_TIMEOUT_S)
    text = result.text or ""
    return (text if text.strip() else None), result


def _check_key_claims(claims) -> None:
    """``key_claims`` must be a list of objects, each with a string 'claim'."""
    if not isinstance(claims, list) or not all(
        isinstance(c, dict) and isinstance(c.get("claim"), str) for c in claims
    ):
        raise ValueError("key_claims must be a list of objects with a string 'claim'")


def _check_confidence(confidence) -> None:
    """``confidence`` must be a number in [0.0, 1.0]; a JSON boolean is not one."""
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) \
            or not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be a number in [0.0, 1.0], got {confidence!r}")


def _check_string_lists(parsed: dict) -> None:
    """``risks``, ``gaps`` and ``questions``, when present, are lists of strings."""
    for field in ("risks", "gaps", "questions"):
        value = parsed.get(field, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{field} must be a list of strings")


def _validate_live_fields(parsed: dict) -> None:
    """Type-check a live seat's parsed JSON — presence alone (the caller's
    missing-field check) is not enough: a model can return every required
    key with the WRONG type (``key_claims`` as a list of strings,
    ``confidence`` as a string, ``risks`` as a bare string instead of a
    list), and that reached ``synthesise`` uncaught, crashing the WHOLE run
    and losing every other seat's paid reading too (GRIP#9187 review).
    Raises ``ValueError`` on any violation so the caller folds this seat
    into the ordinary stub fallback instead."""
    if not isinstance(parsed.get("structural_map"), str):
        raise ValueError("structural_map must be a string")
    _check_key_claims(parsed.get("key_claims"))
    _check_confidence(parsed.get("confidence"))
    _check_string_lists(parsed)


def _parsed_live_result(model: str, address: str, text: str, result) -> dict:
    """Validate + tag one live seat's parsed JSON reply.

    A reply that holds no JSON object is named in the error: its length and
    its first and last 120 characters. Grok 4.6 fell back this way in 6 of 20
    runs on 2026-11-16 and in none of 7 captured alone, so without the reply
    the cause could not be told apart (GRIP#9229)."""
    try:
        parsed = json_object(text)
    except ValueError as exc:
        raise ValueError(f"{exc}; the {len(text):,}-character reply began {text[:120]!r} "
                         f"and ended {text[-120:]!r}") from None
    for field in ("structural_map", "key_claims", "confidence"):
        if field not in parsed:
            raise ValueError(f"missing field {field}")
    _validate_live_fields(parsed)
    parsed["model"] = model
    parsed["address"] = address
    parsed["served_model"] = result.model  # may be None — never substitute the address
    parsed["provider"] = result.provider
    parsed["source"] = "live"
    return parsed


def _dispatch_real(model: str, target: str, content: str,
                   adapter_factory: Optional[Callable] = None) -> dict:
    """Live HAL dispatch, one real provider call per seat (GRIP#9187).

    ``model`` resolves to a HAL address via lib/council_seat.model_address
    and the seat is PINNED (no_failover=True): an unserved pin comes back
    empty, never quietly answered by another model. Any failure (unserved,
    parse error, adapter exception, missing fields) falls back to the
    labelled stub (fail-open); a stub never outvotes a live seat.

    adapter_factory: dependency-injection seam for tests.
    """
    factory = _resolve_adapter_factory(model, adapter_factory)
    if factory is None:
        return _dispatch_stub(model, target, content, reason="adapter not importable")

    address = ""
    try:
        address = model_address(model)
        if not address.strip():
            # A blank or whitespace-only label resolves to a blank address.
            # main() refuses that before it gets here, but a programmatic
            # caller of run()/_dispatch_real is not gated by the CLI parser,
            # and a blank model would otherwise reach adapter.infer(model="")
            # and be answered by HAL's own default route (GRIP#9187 review).
            _warn(f"{model!r}: resolved to an empty address; the seat has no live reading")
            return _dispatch_stub(model, target, content, reason="empty address",
                                  address=address)
        text, result = _call_seat(factory(), address, target, content)
        if text is None:
            _warn(f"{model}: {address} was not served; the seat has no live reading")
            return _dispatch_stub(model, target, content, reason="not served", address=address)
        return _parsed_live_result(model, address, text, result)
    except Exception as exc:  # fail-open by design; the reason is printed, never swallowed
        _warn(f"{model}: live dispatch failed ({type(exc).__name__}: {exc}); using the stub")
        return _dispatch_stub(model, target, content, reason="live dispatch failed", address=address)


def _select_dispatch() -> Callable[[str, str, str], dict]:
    """Choose dispatch path at call time so env-var toggle takes effect in tests."""
    return _dispatch_real if os.environ.get("ULTRAREAD_USE_HAL", "1") == "1" else _dispatch_stub


def _dedupe_models(models: list[str]) -> list[str]:
    """Drop a later ``--models`` entry whose RESOLVED address repeats an
    earlier one — the same label twice, or two different labels that
    resolve to one address. Warns and keeps the first. A seat dispatched
    twice to the identical address is one model voting as two: GRIP#9187's
    class surviving the per-label fix (review finding, lib/ultraread.py
    agreement scoring)."""
    seen: dict[str, str] = {}
    deduped: list[str] = []
    for m in models:
        addr = model_address(m)
        if addr in seen:
            _warn(f"{m}: resolves to the same address as {seen[addr]!r} "
                  f"({addr}); dropping the duplicate seat")
            continue
        seen[addr] = m
        deduped.append(m)
    return deduped


def _make_slug(target: str) -> str:
    """Convert target path/URL to filesystem-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", target)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:60] or "target"


def _validate_result(result: dict) -> None:
    """Raise ValueError with a clear message if required fields are missing."""
    for field in ("model", "key_claims", "confidence"):
        if field not in result:
            raise ValueError(
                f"Per-model result missing required field '{field}'. "
                f"Got keys: {list(result.keys())}"
            )


def _claim_pairs(results: list[dict]):
    """Yield ``(claim_text, model)`` for every claim of every result — one
    flat comprehension (a single AST pass, not a nested loop) rather than
    for-in-for, same O(n*c) cost."""
    return (
        (item.get("claim", "").strip(), r["model"])
        for r in results
        for item in r.get("key_claims", [])
    )


# Claims match on the identifiers they cite (GRIP#9204). Independent models
# word one fact differently, so claims compared as exact text almost never
# matched: the first live run with four distinct models scored 0% on a file
# whose six top-level constructs every seat had named.
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TICKED_NAME = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)(?:\(\))?`")
_CALLED = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\((?!s\))")
_PLURAL_ACRONYM = re.compile(r"[A-Z]+s")
# Where a text writes a name as code: a definition or import, or a dotted chain.
_DEFINED = re.compile(r"\b(?:class|def|function|interface|struct|enum|type)\s+([A-Za-z_][A-Za-z0-9_]*)")
_IMPORTED = re.compile(r"\bimport\s+([A-Za-z_][A-Za-z0-9_, ]*)")
_DOTTED_CHAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")


def _code_shaped(word: str) -> bool:
    """True for snake_case, _private, CONSTANT_CASE, and camelCase or
    PascalCase with an inner capital: shapes an English word does not take.
    Two letters are too few to tell (``zA`` from a ``[a-zA-Z]`` class), and a
    plural acronym (``SKUs``, ``APIs``) is English; a live run on 2026-11-16
    counted both as identifiers."""
    if len(word) < 3 or _PLURAL_ACRONYM.fullmatch(word):
        return False
    inner_capital = any(c.isupper() for c in word[1:]) and any(c.islower() for c in word)
    return "_" in word or inner_capital


def _cited_identifiers(claim: str) -> frozenset:
    """The identifiers a claim cites: every code-shaped word, the last part of
    each backticked name, and each name written as a call (``seat(s)`` is a
    plural, not a call)."""
    shaped = {w for w in _WORD.findall(claim) if _code_shaped(w)}
    ticked = {name.rsplit(".", 1)[-1] for name in _TICKED_NAME.findall(claim)}
    return frozenset(shaped | ticked | set(_CALLED.findall(claim)))


def _mixed_case_only(name: str) -> bool:
    """Code-shaped only by an inner capital (``LinkedIn``, ``GitHub``): the
    shape a brand name takes in prose. snake_case and CONSTANT_CASE are not."""
    return "_" not in name and _code_shaped(name)


def _code_context_names(text: str) -> frozenset:
    """Every name the text itself writes as code: backticked, called, defined,
    imported, or part of a dotted chain."""
    ticked = chain.from_iterable(name.split(".") for name in _TICKED_NAME.findall(text))
    imported = chain.from_iterable(_WORD.findall(group) for group in _IMPORTED.findall(text))
    dotted = chain.from_iterable(c.split(".") for c in _DOTTED_CHAIN.findall(text))
    return frozenset(chain(ticked, imported, dotted, _CALLED.findall(text), _DEFINED.findall(text)))


def _identifier_reader(target: str = "", content: str = "") -> Callable[[str], frozenset]:
    """How this run reads a claim's identifiers: the names it cites, less the
    target's own file name (every seat was told it), and, when the text the
    seats read is known, only names that occur in it, so a name no seat was
    shown there never makes two seats agree. A name whose only code shape is
    an inner capital counts only where that text writes it as code: in prose,
    LinkedIn is a word, and as an identifier it let every claim that mentions
    it back every other (GRIP#9553)."""
    own_name = frozenset(_WORD.findall(Path(target).name))
    window = content[:PROMPT_CHARS]
    shown = frozenset(_WORD.findall(window)) if content else None
    as_code = _code_context_names(window)

    def read(claim: str) -> frozenset:
        cited = _cited_identifiers(claim) - own_name
        if shown is None:
            return cited
        return frozenset(n for n in cited & shown if n in as_code or not _mixed_case_only(n))
    return read


def _normalised(claim: str) -> str:
    """A claim compared without case, spacing or a closing full stop."""
    return " ".join(claim.casefold().split()).rstrip(".")


# A claim that names no identifier matches on the uncommon words it shares
# with one claim of another seat (pre-registered as H1008). It used to match
# on its exact text alone, so on a prose target paraphrases never agreed: on
# 2026-11-24 four seats read a three-section prose thread, plainly made the
# same claims in their own words, and the agreement line read 0%. The rule and
# its numbers were chosen by measurement, recorded in the pull request: among
# the candidates that separated that run's shape, the overlap coefficient
# also matched different claims from stored code runs, and weighting by
# log(1 + N / n) let two short claims that shared only a name and a verb match.
_PROSE_TOKEN = re.compile(r"\w+")  # Unicode word characters: "café" and "Zoë" stay whole
_PROSE_STEM = 6         # "article" and "articles" are one word
_PROSE_MIN_SHARED = 3   # a person's name is often two words, so two can be the name alone
_PROSE_MATCH = 0.25     # the shared words' share of the weight of all words in either claim
_FUNCTION_WORDS = frozenset("""
a an the and or but nor of to in on at by for with from as into onto about over
under after before than then so such that this these those there here is are was
were be been being am it its he she they them his her hers their theirs him we us
our you your i me my mine who whom whose which what when where why how not no also
has have had having do does did done can could would should will shall may might
must very more most only just own same other some any each all both either neither
if while because though although whether
""".split())
# The verbs seats use to attribute a claim ("Jane Example claims ...") say who
# said it, not what was said, and each seat picks its own.
_REPORTING_VERBS = frozenset(
    "claims claimed says said states stated asserts asserted argues argued".split())
_NOT_COMPARED = _FUNCTION_WORDS | _REPORTING_VERBS


def _prose_words(claim: str) -> frozenset:
    """The words a claim is compared on by the word rule: lowercased, two
    letters or more, without function words or reporting verbs, each cut to
    its first six letters."""
    return frozenset(token.casefold()[:_PROSE_STEM] for token in _PROSE_TOKEN.findall(claim)
                     if len(token) > 1 and token.casefold() not in _NOT_COMPARED)


def _word_weights(word_sets: list[frozenset]) -> dict[str, float]:
    """Each word's weight, log(N / n), where N is the number of distinct claims
    in the run and n the number that use the word: a word every claim uses,
    such as the name of the person a text is about, weighs nothing."""
    uses = Counter(word for words in word_sets for word in words)
    return {word: math.log(len(word_sets) / n) for word, n in uses.items()}


def _same_prose_claim(words: frozenset, other: frozenset, weight: dict[str, float]) -> bool:
    """True when two claims share at least three words, and the shared words
    carry at least a quarter of the weight of all the words either uses."""
    shared = words & other
    if len(shared) < _PROSE_MIN_SHARED:
        return False
    either = sum(weight[w] for w in words | other)
    return either > 0 and sum(weight[w] for w in shared) >= _PROSE_MATCH * either


def _backs_in_words(words: frozenset, seat_claims: list[dict], weight: dict[str, float]) -> bool:
    """True when ONE of a seat's claims matches ``words``. Claim to claim, never
    the seat's words pooled across its claims: pooled, a name that appears in
    most claims would let a seat back every claim that mentions it."""
    return any(_same_prose_claim(words, claim["words"], weight) for claim in seat_claims)


def _is_prose_claim(claim: str) -> bool:
    """True when a claim names no identifier at all, so the word rule judges
    it. A claim whose names the identifier rule refused (not in the target, or
    the target's own name) is not one: it still matches on its exact text
    alone, so a name no seat was shown cannot come back as words."""
    return not _cited_identifiers(claim)


def _judged_by_words(entry: dict) -> bool:
    """True when the word rule judges this claim: no spelling of it names an
    identifier, and the run's reader found none either. The support rule and
    the count on the agreement line both ask this one question, so the count
    is true by construction, whatever reader the caller passes."""
    return entry["prose"] and not entry["ids"]


def _claim_entries(results: list[dict], read_ids: Callable[[str], frozenset]):
    """One pass over every claim. Returns ``(entries, seat_ids, seat_claims)``:
    one entry per distinct claim (its first wording, the seats that made it,
    the identifiers it cites, whether the word rule judges it, its words),
    every identifier each seat's claims cite, and each seat's own entries. A
    seat that repeats a claim counts once for it (GRIP#9187 review).

    Seats can spell one claim with different capitals, and only some
    spellings may be code-shaped ("GitHub", "Github"). Every spelling counts,
    so the result does not depend on seat order: the claim cites every
    identifier any spelling cites, the word rule judges it only when no
    spelling names one, and each seat is credited only with the identifiers
    its own spelling cites."""
    entries: dict[str, dict] = {}
    seat_ids: dict[str, set] = {}
    seat_claims: dict[str, list] = {}
    for text, model in _claim_pairs(results):
        if text:
            ids = read_ids(text)
            entry = entries.setdefault(_normalised(text),
                                       {"text": text, "said": set(), "ids": frozenset(),
                                        "prose": True, "words": _prose_words(text)})
            entry["said"].add(model)
            entry["ids"] |= ids
            entry["prose"] = entry["prose"] and _is_prose_claim(text)
            seat_ids.setdefault(model, set()).update(ids)
            seat_claims.setdefault(model, []).append(entry)
    return entries, seat_ids, seat_claims


def _supporters(entry: dict, order: list[str], seat_ids: dict[str, set],
                seat_claims: dict[str, list], weight: dict[str, float]) -> list[str]:
    """The seats, in seat order, that made this claim, or that back it: by
    citing every identifier it cites, or, when it names none, with one claim
    of their own that shares enough of its uncommon words."""
    backers = set(entry["said"])
    if entry["ids"]:
        backers |= {m for m, cited in seat_ids.items() if entry["ids"] <= cited}
    elif _judged_by_words(entry):
        backers |= {m for m, claims in seat_claims.items()
                    if _backs_in_words(entry["words"], claims, weight)}
    return [m for m in order if m in backers]


def _claim_support(results: list[dict], read_ids: Callable[[str], frozenset]):
    """``(claim_map, prose_claims)``: the map _collect_claim_map returns, and
    how many of its distinct claims the word rule judges."""
    entries, seat_ids, seat_claims = _claim_entries(results, read_ids)
    weight = _word_weights([entry["words"] for entry in entries.values()])
    order = [r["model"] for r in results]
    claim_map = {entry["text"]: _supporters(entry, order, seat_ids, seat_claims, weight)
                 for entry in entries.values()}
    return claim_map, sum(1 for entry in entries.values() if _judged_by_words(entry))


def _collect_claim_map(results: list[dict],
                       read_ids: Callable[[str], frozenset] = _cited_identifiers) -> dict[str, list[str]]:
    """Build {claim_text: [model, ...]}: for each distinct claim, the seats
    that support it (GRIP#9204).

    A seat supports a claim when it made the same claim, or when its own
    claims cite every identifier this one cites. That measures shared
    attention to the same constructs, not shared conclusions: two seats that
    say opposite things about one function still support each other. A claim
    that names no identifier is supported by a seat with one claim that shares
    enough of its uncommon words (see _same_prose_claim), which is likewise
    shared attention, not a shared conclusion. A claim whose every name was
    refused (see _identifier_reader) matches on its exact text alone."""
    return _claim_support(results, read_ids)[0]


def compute_agreement_score(results: list[dict],
                            read_ids: Callable[[str], frozenset] = _cited_identifiers) -> dict:
    """Compute cross-model agreement on key_claims: the share of distinct
    claims that every seat supports (see _collect_claim_map).

    Returns dict with score (0.0-1.0), total_claims, unanimous_claims, and
    prose_claims, the distinct claims that name no identifier and so are
    matched on their words. Empty results return score 0.0.
    """
    if not results:
        return {"score": 0.0, "total_claims": 0, "unanimous_claims": 0, "prose_claims": 0}
    claim_map, prose = _claim_support(results, read_ids)
    n = len(results)
    unanimous = sum(1 for ms in claim_map.values() if len(ms) == n)
    total = len(claim_map)
    return {
        "score": (unanimous / total) if total > 0 else 0.0,
        "total_claims": total,
        "unanimous_claims": unanimous,
        "prose_claims": prose,
    }


def _served_identity(result: dict) -> str:
    """The model a live seat ACTUALLY got, for counting distinct models only.

    The served model id with any ``provider/`` prefix removed, lowercased; the
    resolved address (then the label) when HAL reported no served model. The
    ``provider`` field is never used here: on the HAL gateway path it is the
    REQUESTED prefix, not the server's (``hal_llm_adapter._gateway_reply``),
    so prefixing it would make two pins served by one model look like two.
    The header still shows each seat's real ``served_model`` as reported."""
    served = result.get("served_model") or result.get("address") or result.get("model") or ""
    return served.strip().lower().rsplit("/", 1)[-1]


def _distinct_voters(voters: list[dict]) -> list[dict]:
    """The first live seat per served model.

    A later seat served by the same model is one model voting twice, the
    GRIP#9187 class surviving the per-label fix: it is dropped from the vote
    with a warning, and the **Models** header still names it. An all-stub
    voter set, which only the explicit offline mode produces, is returned
    unchanged: canned stub text has no served model to compare."""
    if not voters or not _is_live(voters[0]):
        return voters
    first: dict[str, str] = {}
    kept: list[dict] = []
    for r in voters:
        key = _served_identity(r)
        if key in first:
            _warn(f"{r['model']}: served by the same model as {first[key]} ({key}); "
                  "it counts once in the agreement")
            continue
        first[key] = r["model"]
        kept.append(r)
    return kept


def _match_rule(agreement: dict) -> str:
    """How this run's claims were matched. When some cite no identifier, it
    says how many, so a reader can tell that the word rule, not the
    identifier rule, judged them."""
    prose = agreement.get("prose_claims", 0)
    if not prose:
        return "claims match on the identifiers they cite"
    return (f"claims match on the identifiers they cite, or, citing none "
            f"({prose} of {agreement['total_claims']}), on the uncommon words they share")


def _agreement_line(n: int, live_run: bool, agreement: dict) -> str:
    """The '**Cross-model agreement**' header line. A live run with fewer
    than two distinct models voting renders 'n/a' rather than a percentage
    that would compare a model against itself."""
    if live_run and n < 2:
        return (f"**Cross-model agreement**: n/a (fewer than two distinct live models) "
                f"({n} models, {agreement['total_claims']} claims, "
                f"{agreement['unanimous_claims']} unanimous)")
    score_pct = int(agreement["score"] * 100)
    return (f"**Cross-model agreement**: {score_pct}% "
            f"({n} models, {agreement['total_claims']} claims, "
            f"{agreement['unanimous_claims']} unanimous; {_match_rule(agreement)})")


_SUPPORT_NOTE = ("_A seat supports a claim when it made it, or when its own claims cite "
                 "every identifier in it: shared attention to the same code, not proof "
                 "of the same conclusion. A claim citing no identifier is supported by a "
                 "seat with one claim that shares enough of its uncommon words, which is "
                 "likewise shared attention, not proof._")


def _render_claims(claim_map: dict[str, list[str]], n: int, all_models: list[str]) -> list[str]:
    """Render ## Key claims and ## Dissents sections from pre-built claim_map,
    whose lists name the seats that support each claim. The note under the
    heading says what a tag counts, where the reader sees the tags (AGORA on
    GRIP#9218: two seats saying opposite things about one function agree)."""
    model_set = set(all_models)
    lines: list[str] = ["## Key claims", "", _SUPPORT_NOTE, ""]
    for claim, cited in claim_map.items():
        tag = "unanimous" if len(cited) == n else f"{len(cited)}/{n}"
        lines.append(f"- [{tag}] {claim}")
    lines.append("")
    dissents = {c: ms for c, ms in claim_map.items() if len(ms) < n}
    if dissents:
        lines += ["## Dissents", ""]
        for claim, cited in dissents.items():
            absent = sorted(model_set - set(cited))
            lines.append(f"- **{claim}** -- supported by {cited}; not by {absent}")
        lines.append("")
    return lines


def _collect_field(results: list[dict], field: str) -> list[str]:
    """Flatten a single field across all model results into a list of strings."""
    out: list[str] = []
    for r in results:
        out.extend(r.get(field, []))
    return out


def _render_flat(results: list[dict], heading: str, *fields: str) -> list[str]:
    """Render a section heading + flat bullet list from one or more fields."""
    items: list[str] = []
    for f in fields:
        items.extend(_collect_field(results, f))
    return [heading, ""] + [f"- {item}" for item in items] + [""]


def _is_live(result: dict) -> bool:
    """True when ``result`` came from a real model call. A result with no
    ``source`` key — every test fixture built before this field existed —
    counts as live; only an explicit ``source: "stub"`` opts a seat out of
    voting."""
    return result.get("source", "live") == "live"


def _voters(results: list[dict]) -> list[dict]:
    """Results that vote on claims / agreement / confidence: the live seats
    when any exist, else every result (an all-stub run, which only the
    explicit offline mode can produce now, still renders its labelled stubs
    as before)."""
    live = [r for r in results if _is_live(r)]
    return live or results


def _model_label(result: dict) -> str:
    """One **Models** header entry for one seat — a live seat names what was
    served, a stub names why it was not."""
    if _is_live(result):
        served = result.get("served_model")
        return f"{result['model']} (served: {served})" if served \
            else f"{result['model']} (served model not reported)"
    return f"{result['model']} (stub: {result.get('reason', 'unknown')})"


def _stub_banner(results: list[dict], n_voters: int) -> list[str]:
    """Banner lines when any seat is a labelled stub — [] when every seat voted."""
    stubs = [r for r in results if not _is_live(r)]
    if not stubs:
        return []
    left_out = (f" and left out of the {n_voters}-model agreement score"
                if n_voters < len(results) else "")
    return [
        (f"> **{len(stubs)} of {len(results)} seats came from the labelled stub "
         f"rather than a model call**{left_out}."),
        "",
    ]


def _seats_without_live_reading(results: list[dict]) -> list[str]:
    """## Seats without a live reading — only when a stub sits beside a live
    seat (an all-stub run has nothing to contrast it with)."""
    stubs = [r for r in results if not _is_live(r)]
    if not stubs or len(stubs) == len(results):
        return []
    lines = ["## Seats without a live reading", ""]
    for r in stubs:
        address = r.get("address") or "address unresolved"
        lines.append(f"- **{r['model']}** ({address}): {r.get('reason', 'unknown')}")
    lines.append("")
    return lines


def _voting_context(voters: list[dict],
                    read_ids: Callable[[str], frozenset] = _cited_identifiers) -> tuple[int, bool, dict]:
    """The voting shape, computed once: how many models vote, whether this
    is a live run, and the claim agreement score."""
    agreement = compute_agreement_score(voters, read_ids)
    live_run = bool(voters) and _is_live(voters[0])
    return len(voters), live_run, agreement


def _repeat_banner(results: list[dict], n_voters: int) -> list[str]:
    """Banner lines when a live seat was left out of the vote because another
    seat had already given the same model -- [] otherwise."""
    repeats = sum(1 for r in results if _is_live(r)) - n_voters
    if repeats <= 0:
        return []
    return [
        (f"> **{repeats} live seat(s) repeated a model another seat already gave**; "
         "each model counts once in the agreement."),
        "",
    ]


def _header_lines(target: str, results: list[dict], ts: str,
                  n: int, live_run: bool, agreement: dict) -> list[str]:
    """The deliverable's header block: title, timestamp, **Models**, the
    agreement line, and the banners when a seat degraded or repeated a model."""
    return [
        f"# UltraRead: {target}", "",
        f"**Generated**: {ts}  ",
        f"**Models**: {', '.join(_model_label(r) for r in results)}  ",
        _agreement_line(n, live_run, agreement),
        "",
    ] + _stub_banner(results, n) + _repeat_banner(results, n)


def synthesise(target: str, results: list[dict], content: str = "") -> str:
    """Build the Markdown deliverable from per-model results.

    Claims, agreement, structural map, risks, gaps and per-model confidence
    are computed over the VOTING results only: the live seats when any
    exist, one per served model -- a stub never outvotes a live seat, and a
    model never votes twice. The **Models** header and the optional "Seats
    without a live reading" section still name every seat, live or stub.
    ``content`` is the target's text: when given, a claim's identifiers count
    only where they occur in what the seats were shown (GRIP#9204).
    """
    for r in results:
        _validate_result(r)

    voters = _distinct_voters(_voters(results))
    read_ids = _identifier_reader(target, content)
    claim_map = _collect_claim_map(voters, read_ids)
    n, live_run, agreement = _voting_context(voters, read_ids)
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    header = _header_lines(target, results, ts, n, live_run, agreement)
    structural = (
        ["## Structural map", ""]
        + [f"- **{r['model']}**: {r.get('structural_map', '')}" for r in voters]
        + [""]
    )
    confidence = (
        ["## Per-model confidence", ""]
        + [" · ".join(f"{r['model']} {r['confidence']:.2f}" for r in voters)]
        + [""]
    )
    sections = (
        header + structural
        + _render_claims(claim_map, n, [r["model"] for r in voters])
        + _render_flat(voters, "## Risks", "risks")
        + _render_flat(voters, "## Gaps & questions", "gaps", "questions")
        + confidence
        + _seats_without_live_reading(results)
    )
    return "\n".join(sections)


def _dispatch_all(dispatch_fn: Callable, models: list[str], target: str, content: str,
                  adapter_factory: Optional[Callable]) -> list[dict]:
    """Run every seat's dispatch IN PARALLEL, results in the same order as
    ``models``. ``adapter_factory`` is forwarded only on the live path —
    ``_dispatch_stub`` takes no such parameter."""
    live_path = dispatch_fn is _dispatch_real
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(models)) as pool:
        if live_path:
            futures = [pool.submit(dispatch_fn, m, target, content,
                                   adapter_factory=adapter_factory) for m in models]
        else:
            futures = [pool.submit(dispatch_fn, m, target, content) for m in models]
        return [f.result() for f in futures]


def run(target: str, models: Optional[list[str]] = None, content: str = "",
       adapter_factory: Optional[Callable] = None) -> Path:
    """Orchestrate: dispatch (in parallel), synthesise, write deliverable.

    On the live path (``ULTRAREAD_USE_HAL`` unset or ``"1"``), raises
    :class:`UltraReadRefused` and writes nothing when every seat degraded to
    the stub — an all-stub deliverable would wear a council's clothes while
    containing no model's reading at all.
    """
    models = _dedupe_models([canonical_label(m.strip()) for m in (models or DEFAULT_MODELS)])
    if len(content) > PROMPT_CHARS:
        _warn(f"the target is {len(content):,} characters; each model reads only the first {PROMPT_CHARS:,}")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    dispatch_fn = _select_dispatch()
    results = _dispatch_all(dispatch_fn, models, target, content, adapter_factory)
    if dispatch_fn is _dispatch_real and not any(_is_live(r) for r in results):
        raise UltraReadRefused(
            "UltraRead: no seat gave a live reading — every model call "
            "degraded to the stub. Refusing to emit a deliverable with no "
            "live analysis in it. (Set ULTRAREAD_USE_HAL=0 for an explicit, "
            "labelled stub run.)"
        )
    md = synthesise(target, results, content)
    slug = _make_slug(target)
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    out = STATE_DIR / f"{slug}-{ts}.md"
    out.write_text(md, encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-model comprehension council")
    parser.add_argument(
        "target",
        help="File to analyse (a spec, a source file or a saved PR diff). "
             "Anything that is not a readable file is refused.",
    )
    parser.add_argument("--models", help="Comma-separated model list")
    # --council was REMOVED 2026-08-17: it was declared but never read (a
    # nonsense profile was silently accepted with no roster change), and the
    # dispatch layer routes per-adapter, not per-catalogue-tier — a profile
    # resolving to more model LABELS would render models that were never
    # called. Explicit rosters go through --models.
    args = parser.parse_args()

    models = None
    if args.models is not None:
        models = [m.strip() for m in args.models.split(",")]
        if any(not m for m in models):
            parser.error("--models has an empty entry (check for a stray or trailing comma)")
    target_path = Path(args.target)
    if not target_path.is_file():
        # Every seat would otherwise read an empty target and still be a paid
        # call: a reading of nothing is not a reading (GRIP#9187 review).
        parser.error(f"{args.target!r} is not a readable file; /ultraread reads a file's content")
    content = target_path.read_text(encoding="utf-8", errors="replace")

    try:
        out = run(args.target, models, content)
    except UltraReadRefused as exc:
        print(f"[ultraread] REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
    print(f"UltraRead deliverable: {out}")


if __name__ == "__main__":
    main()
