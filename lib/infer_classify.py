"""infer_classify — ask a cheap model what text MEANS, instead of matching substrings.

The motivating failure, measured on 2026-08-31. `next_move.is_delegable` decided
whether a task needed the operator personally by matching substrings: "account",
"bank", "legal", "secret", "payment". It misclassified **7 of 8** ordinary titles.
An `account_manager.py` refactor, the accounting module, a databank loader, the
legal-ops skill and a payment-webhook FIXTURE were all marked operator-only and
would have been silently removed from every execution pool.

The repair was word-boundary action phrases, and it took three tuning rounds to
reach 0 false positives on a ten-title corpus. That is the whole problem: each
round of tuning buys one corpus and the next unusual title breaks it again.
Substring and regex matching cannot distinguish a concept from an incidental
occurrence inside an identifier, and adding cases is YAGNI-violating complexity
that never converges.

A model reads the sentence. Asked the same question about the same title, it
answers "No" — correctly — because it can see that `account_manager.py` is a
filename and not a request to open an account.

## What this is NOT for, and this boundary is load-bearing

**Never use this as a safety gate.** `rules/llm-augmentation-pattern.md` is
PARAMOUNT and lists "no safe mechanical floor" as a category error: if the gate
fails open when the model is down, the gate never existed. That rule governs
ALLOW/DENY decisions where one verdict is strictly safer — guardrail evasion,
secrets, stub detection. For those, keep the deterministic floor and add an LLM
layer that may only tighten. That pattern already exists; do not use this module.

This module is for the OTHER class: classifications where being wrong is merely
inefficient. Routing hints, categorisation, "does this text mean X". The
disqualifier from that same rule, applied here: if the worst-case error breaks a
guarantee the system SELLS, do not use this. If it merely mis-routes some work,
this is the right tool.

## The two properties that make it usable

1. **A declared default.** `default` is a required keyword argument. The caller
   must say what happens when inference is unavailable — offline, timed out,
   rate-limited, or garbage. There is no silent failure mode and no guessing.
2. **Cached by content.** Identical (text, question, labels) returns the cached
   label without a call. Repeats are free, and tests are deterministic without
   mocking the network.

## The decision-model rung, asked first

Before the chat ladder, the question goes to HAL's decision model (Jev, through
``hal_llm_adapter._query_grip_decide``; HAL docs/decide.md) as one typed
question: a Noul for exactly ``("yes", "no")``, a Choice among the labels
otherwise. It answers in about half a second and returns a probability, not a
sentence to parse. Its answer counts as a verdict only outside TypeSafe's own
uncertain band (0.30 to 0.70, or a Choice's top option below 0.70). Inside the
band, or when it cannot answer at all, the chat ladder decides as before.

``source`` says which: ``decide`` (the decision model's verdict), ``model``
(the chat ladder's), ``cache``, ``uncertain`` (the decision model was unsure
and nothing else answered) or ``default`` (nothing answered). A caller that
pins ``model`` skips the rung, and ``GRIP_CLASSIFY_NO_DECIDE=1`` skips it
everywhere, so one backend can be measured against the other.

## H798's records

Each call that reaches a rung leaves one content-free row in
``logs/infer-classify-decide.jsonl`` (lib/infer_classify_telemetry.py): the
text's digest, never the text, the rung that decided, Jev's probabilities, and
each rung's latency, cost and model. After a Jev verdict a detached child asks
the chat ladder too, at most 60 times a UTC day, so that the two rungs'
agreement can be measured. ``GRIP_DECIDE_SHADOW_LADDER=0`` turns the child off.
"""
from __future__ import annotations

import hashlib
import json
import re
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import infer_classify_telemetry as _telemetry  # noqa: E402 -- H798's content-free records

try:
    from claude_dir import get_claude_dir
    _CACHE = get_claude_dir() / "state" / "infer-classify-cache.json"
except Exception:                                               # pragma: no cover
    _CACHE = Path.home() / ".claude" / "state" / "infer-classify-cache.json"

_MEM: dict[str, str] = {}
_DISABLE_ENV = "GRIP_INFER_CLASSIFY_OFFLINE"
#: Skips the decision-model rung, so the chat ladder answers alone.
_NO_DECIDE_ENV = "GRIP_CLASSIFY_NO_DECIDE"
#: A decision-model probability between these is not a verdict: TypeSafe's own
#: guidance sends its 0.30-0.70 band to a human. A Choice's top option must
#: clear the upper bar.
_DECIDED_ABOVE, _DECIDED_BELOW = 0.70, 0.30


def _remember(key: str, label: str, use_cache: bool) -> None:
    if use_cache:
        _MEM[key] = label
        _store(key, label)


def _yes_no(labels: Sequence[str]) -> bool:
    return [label.lower() for label in labels] == ["yes", "no"]


def _decision_question(question: str, labels: Sequence[str]) -> dict:
    """One typed question: a Noul for exactly yes/no, a Choice among the labels otherwise."""
    if _yes_no(labels):
        return {"type": "noul", "instructions": question}
    return {"type": "choice", "instructions": question,
            "criteria": {label: None for label in labels}}


def _verdict(answer: dict, labels: Sequence[str]) -> tuple[Optional[str], str]:
    """``(label, "")`` for a verdict, ``(None, "uncertain")`` inside the band, ``(None, "default")`` otherwise."""
    if _yes_no(labels):
        p = answer.get("noul")
        if isinstance(p, bool) or not isinstance(p, (int, float)):
            return None, "default"
        if p >= _DECIDED_ABOVE:
            return labels[0], ""
        return (labels[1], "") if p <= _DECIDED_BELOW else (None, "uncertain")
    chosen = answer.get("choice")
    top = (answer.get("probabilities") or {}).get(chosen)
    if chosen not in labels or isinstance(top, bool) or not isinstance(top, (int, float)):
        return None, "default"
    return (chosen, "") if top >= _DECIDED_ABOVE else (None, "uncertain")


def _ask_decision_model(text: str, question: str, labels: Sequence[str],
                        record: Optional[dict] = None) -> tuple[Optional[str], str]:
    """The decision-model rung. ``(None, why)`` whenever it gives no verdict; it never raises.

    The privacy floor runs first (lib/decision_privacy.py): text from the
    guardian repository, or text the shield has touched or would touch, never
    reaches a decision model, and the chat ladder answers instead. ``record``
    is H798's call record (lib/infer_classify_telemetry.py), or None.
    """
    try:
        from decision_privacy import may_send
        allowed, why = may_send(text)
        if not allowed:
            _telemetry.note_floor(record, why)
            return None, "default"
        import hal_llm_adapter as hal
        started = _telemetry.clock()
        result = None
        try:
            result = hal._query_grip_decide(text, {"q": _decision_question(question, labels)})
        finally:
            _telemetry.note_rung(record, "decide", started, result)
    except Exception:  # noqa: BLE001 -- the chat ladder below still answers
        return None, "default"
    if not result or not result.get("ok"):
        return None, "default"
    answer = (result.get("answers") or {}).get("q") or {}
    _telemetry.note_answer(record, answer, labels)
    return _verdict(answer, labels)


def cache_key(text: str, question: str, labels: Sequence[str]) -> str:
    """Content hash over exactly the inputs that can change the answer."""
    blob = json.dumps({"t": text, "q": question, "l": list(labels)},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _load() -> dict:
    if not _CACHE.exists():
        return {}
    try:
        data = json.loads(_CACHE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}          # a corrupt cache is a cold cache, never an error


def _store(key: str, label: str) -> None:
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        data = _load()
        data[key] = label
        tmp = _CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=0, sort_keys=True))
        tmp.replace(_CACHE)
    except Exception:
        pass               # caching is an optimisation; never fail the caller


def _build_prompt(text: str, question: str, labels: Sequence[str]) -> str:
    opts = " or ".join(repr(l) for l in labels)
    return (
        f"{question}\n\n"
        f"TEXT: {text}\n\n"
        f"Read the text as a whole and judge what it MEANS, not whether any "
        f"particular word appears in it. A word occurring inside a filename, "
        f"identifier, or unrelated phrase does not make the text about that thing.\n"
        f"Answer with exactly one of: {opts}. Nothing else."
    )


def _parse(raw: str, labels: Sequence[str]) -> Optional[str]:
    """Accept only a declared label. Anything else is a miss, never a guess."""
    s = (raw or "").strip().strip(".\"'` \n").lower()
    for l in labels:
        if s == l.lower():
            return l
    # A model that answered in a sentence gets one chance, but only if exactly
    # ONE label appears -- "yes or no" resolves to nothing, correctly.
    #
    # WORD BOUNDARIES, and this module of all modules must get it right: the
    # first version used `l.lower() in s` and matched "no" inside "cannot", so
    # "I cannot tell" -- an explicit refusal to answer -- resolved to "no". The
    # substring bug this whole module exists to eliminate, inside its own
    # parser. Caught by test_an_ambiguous_answer_is_a_miss_not_a_coin_flip.
    hits = [l for l in labels if re.search(rf"\b{re.escape(l.lower())}\b", s)]
    return hits[0] if len(hits) == 1 else None


def classify_detail(text: str, question: str,
                    labels: Sequence[str] = ("yes", "no"), *,
                    default: str,
                    use_cache: bool = True,
                    model: Optional[str] = None) -> tuple[str, str]:
    """Return `(label, source)`: source is 'cache', 'decide', 'model', 'uncertain' or 'default'.

    A caller that treats "the model said no" and "the model was unreachable so
    you got your default" as the same fact is drawing a conclusion the data
    does not support. `classify()` is the thin wrapper for callers that do not
    need the distinction; this is for those that do. The sources are explained
    in the module docstring, under "The decision-model rung, asked first".
    """
    if default not in labels:
        raise ValueError(f"default {default!r} must be one of {list(labels)}")
    if not (text or "").strip():
        return default, "default"
    key = cache_key(text, question, labels)
    cached = _cached(key, labels) if use_cache else None
    if cached is not None:
        return cached, "cache"
    if os.environ.get(_DISABLE_ENV):
        return default, "default"
    record = _telemetry.start(text, question, labels)
    label, source = _infer(text, question, labels, model, record)
    _telemetry.finish(record, text, question, labels, label, source)
    if label is None:
        # Nothing is cached on this path, deliberately: no answer is not
        # evidence about the text, and caching the default would make one
        # outage permanent for that input.
        return default, source
    _remember(key, label, use_cache)
    return label, source


def _cached(key: str, labels: Sequence[str]) -> Optional[str]:
    if key in _MEM:
        return _MEM[key]
    disk = _load().get(key)
    if disk in labels:
        _MEM[key] = disk
        return disk
    return None


def _infer(text: str, question: str, labels: Sequence[str],
           model: Optional[str], record: Optional[dict] = None) -> tuple[Optional[str], str]:
    """The decision-model rung, then the chat ladder: ``(label, source)``, or ``(None, why)``."""
    unanswered = "default"
    if model is None and not os.environ.get(_NO_DECIDE_ENV):
        decided, unanswered = _ask_decision_model(text, question, labels, record)
        if decided is not None:
            return decided, "decide"
    label = _ask_chat_ladder(text, question, labels, model, record)
    return (label, "model") if label is not None else (None, unanswered)


def _ask_chat_ladder(text: str, question: str, labels: Sequence[str],
                     model: Optional[str], record: Optional[dict] = None) -> Optional[str]:
    """HAL's chat ladder, read back strictly; None on any failure or unparseable answer."""
    try:
        import hal_llm_adapter as hal
        started = _telemetry.clock()
        result = None
        try:
            result = hal._query_grip_infer(_build_prompt(text, question, labels), model=model)
        finally:
            _telemetry.note_rung(record, "model", started, result)
    except Exception:  # noqa: BLE001 -- a transport failure is no answer, never an error
        return None
    return _parse(result.get("text", ""), labels) if result else None


def classify(text: str, question: str,
             labels: Sequence[str] = ("yes", "no"), *,
             default: str,
             use_cache: bool = True,
             model: Optional[str] = None) -> str:
    """Return one of `labels`, or `default` if inference could not answer.

    `default` is required and has no fallback value on purpose: a caller that
    has not decided what offline means has not finished designing the call.
    Use `classify_detail` when you need to know WHICH of those happened.
    """
    return classify_detail(text, question, labels, default=default,
                           use_cache=use_cache, model=model)[0]


def forget(text: str, question: str, labels: Sequence[str] = ("yes", "no")) -> bool:
    """Evict one cached answer. Returns True if anything was removed.

    The cache never expires by design -- a repeat must be free -- which means a
    WRONG answer is permanent until someone evicts it. Measured 2026-09-02: the
    model once read "Push 5 commit(s) that exist on no remote" as needing
    credentials, and that verdict was served from cache on every later call.
    Changing the question text orphans the entry automatically (the key covers
    the question); this is for the case where the question is right and the
    one answer is wrong.
    """
    key = cache_key(text, question, labels)
    hit = _MEM.pop(key, None) is not None
    try:
        data = _load()
        if key in data:
            del data[key]; hit = True
            tmp = _CACHE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=0, sort_keys=True)); tmp.replace(_CACHE)
    except Exception:
        pass
    return hit


def available() -> bool:
    """Whether an inference call would be attempted at all. Cheap, no network."""
    if os.environ.get(_DISABLE_ENV):
        return False
    try:
        import hal_llm_adapter  # noqa: F401
        return True
    except Exception:
        return False
