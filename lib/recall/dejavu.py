"""Recall delight surface — proactive déjà-vu (recall-before-create).

Before an agent builds something, ask the moat whether it already exists:
``check_before_create`` extracts candidate subjects from the intent text,
queries the subject index for each, and — when near-duplicates exist —
renders a déjà-vu card via ``lib.recall.cards``: "you're about to build X —
GRIP built X' on <date>, here it is."

Dependency-injected: ``query_fn`` defaults to a LAZY import of
``lib.recall.index.query_subject`` at call time (delivered by the parallel
index track). The ImportError→RuntimeError translation below is a
documented runtime-dependency boundary, not a stub.
"""

from __future__ import annotations

import re

from lib.recall import cards, schema

_MAX_SUBJECTS = 8
_QUOTED = re.compile(r'["\'`]([^"\'`\n]{2,80})["\'`]')
_CAP_TOKEN = re.compile(r"\b([A-Z][A-Za-z0-9_-]{2,})\b")
_NOUN_PHRASE = re.compile(r"\b(?:a|an|the)\s+((?:[\w-]+\s+){0,2}[\w-]+)", re.I)

# Common imperative/filler tokens that are never a subject on their own.
_STOPWORDS = frozenset(
    "build create make write add implement render generate draft ship "
    "the a an this that those these it its i we you they please now new "
    "first then also let us do and or for with from into about our your "
    "my need want going todo".split()
)


def extract_subjects(intent_text: str, limit: int = _MAX_SUBJECTS) -> list[str]:
    """Candidate recall subjects from free intent text (small on purpose).

    Order of confidence: quoted strings, Capitalised tokens, then simple
    determiner-led noun phrases. Deduped case-insensitively, stopwords out.
    """
    text = intent_text or ""
    raw = _QUOTED.findall(text) + _CAP_TOKEN.findall(text) + _NOUN_PHRASE.findall(text)
    seen: set[str] = set()
    out: list[str] = []
    for cand in (c.strip() for c in raw):
        key = cand.lower()
        if len(cand) < 3 or key in _STOPWORDS or key in seen:
            continue
        seen.add(key)
        out.append(cand)
        if len(out) >= limit:
            break
    return out


def _default_query_fn(store_dir):
    """Runtime-dependency boundary: the index ships on the parallel track."""
    try:
        from lib.recall.index import query_subject
    except ImportError as exc:
        raise RuntimeError(
            "Recall's subject index is not available: the module "
            "lib.recall.index (which provides query_subject) could not be "
            "imported. It is built by the parallel index track — until it "
            "lands, pass query_fn= explicitly to check_before_create."
        ) from exc
    return lambda subject: query_subject(subject, store=store_dir)


def _artefacts_of(result) -> list[dict]:
    """Accept a QueryResult, a QueryResult-dict, or None."""
    if result is None:
        return []
    if hasattr(result, "to_dict"):
        result = result.to_dict()
    arts = result.get("artefacts") or ()
    return [a.to_dict() if hasattr(a, "to_dict") else dict(a) for a in arts]


def _merge_unique(found: list[dict], seen: set[str], artefacts: list[dict]) -> None:
    """Append artefacts not seen before; O(1) set-keyed dedupe, in place."""
    for art in artefacts:
        key = schema.canonical_json(art)
        if key not in seen:
            seen.add(key)
            found.append(art)


def _dejavu_card(intent_text: str, artefacts: list[dict]) -> str:
    first = artefacts[0]
    built = first.get("subject") or first.get("path") or "it"
    when = first.get("created_at") or "an earlier session"
    claim = (
        f"you're about to build \"{intent_text.strip()}\" — "
        f"GRIP built \"{built}\" on {when}; here it is."
    )
    shaped = {
        "verdict": schema.VERDICT_ALLOW,
        "claim": claim,
        "evidence": artefacts,
        "basis": "exact",
        "reason": "déjà-vu: near-duplicate found before creation",
    }
    return cards.render_catch_card(shaped)


def check_before_create(intent_text: str, query_fn=None, store_dir=None) -> dict:
    """Recall-before-create. Returns {"near_duplicates": [...], "card": str|None}.

    ``store_dir`` feeds the default query path only; an injected ``query_fn``
    owns its own storage.
    """
    if query_fn is None:
        query_fn = _default_query_fn(store_dir)
    seen: set[str] = set()
    near: list[dict] = []
    for subject in extract_subjects(intent_text):
        _merge_unique(near, seen, _artefacts_of(query_fn(subject)))
    card = _dejavu_card(intent_text, near) if near else None
    return {"near_duplicates": near, "card": card}
