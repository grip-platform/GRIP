"""Negative-existence claim engine (F5).

A negative claim ("no X exists", "it's gone") is DENIED when the moat holds
an ACTIVE artefact for any extracted subject that STILL VERIFIES on disk
right now; ALLOWED only as an honest scoped negative (explicit non-empty
scope + verified index freshness); otherwise UNVERIFIABLE with the reason.
Basis is always exact/alias/fts — semantic similarity never authorises a
negative claim, so ``basis`` can never be "semantic" by construction.
"""

from __future__ import annotations

import re

from lib.recall import index
from lib.recall.aliases import DEFAULT_ALIASES, normalise
from lib.recall.schema import (
    PreassertResult,
    STATUS_ACTIVE,
    VERDICT_ALLOW,
    VERDICT_DENY,
    VERDICT_UNVERIFIABLE,
)

NEGATIVE_EXISTENCE_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bno\b[^.\n?!]{0,60}?\bexists?\b",
    r"\b(?:does\s*not|doesn'?t|do\s+not|don'?t)\s+exist\b",
    r"\bnot\s+found\s+anywhere\b",
    r"\bnowhere\s+to\s+be\s+found\b",
    r"\b(?:it|is|are|they)'?s?\s+gone\b",
    r"\bnever\s+(?:was\s+|been\s+)?(?:created|built|rendered|generated|made)\b",
    r"\b(?:no|not|never|isn'?t|wasn'?t|missing|lost)\b[^.\n?!]{0,60}\bneeds?\s+(?:re)?build(?:ing)?\b",
    r"\bneeds?\s+rebuild(?:ing)?\b[^.\n?!]{0,60}\b(?:no|not|never|gone|missing|lost)\b",
    r"\b(?:definitely|certainly|clearly|confident(?:ly)?|i'?m\s+sure|there\s+is)\b[^.\n?!]{0,60}\b(?:no|not|never|doesn'?t|gone|missing)\b",
))

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_QUOTED_RE = re.compile(r"[\"'`]([^\"'`]{2,60})[\"'`]")
_CAPS_RE = re.compile(r"\b[A-Z][A-Za-z0-9]{1,40}\b")
_HYPHEN_RE = re.compile(r"\b[a-z0-9]+(?:[-_][a-z0-9]+)+\b")
_STOPWORDS = frozenset({
    "no", "not", "the", "it", "its", "this", "that", "there", "never", "i",
    "a", "an", "is", "are", "was", "were", "on", "in", "at", "of", "and",
    "or", "does", "do", "exists", "exist", "anywhere", "disk", "found",
})
_MAX_SUBJECTS = 12


def is_negative_claim(text: str) -> bool:
    return any(p.search(text) for p in NEGATIVE_EXISTENCE_PATTERNS)


def negative_sentences(text: str) -> list[str]:
    """Sentences of ``text`` that match a negative-existence pattern."""
    parts = [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]
    return [s for s in parts if is_negative_claim(s)]


def _alias_hits(norm_claim: str) -> list[str]:
    padded = f" {norm_claim} "
    tokens = {normalise(k) for k in DEFAULT_ALIASES}
    tokens |= {normalise(a) for vals in DEFAULT_ALIASES.values() for a in vals}
    return [t for t in sorted(tokens) if t and f" {t} " in padded]


def extract_subjects(claim: str) -> list[str]:
    """Subjects worth checking: quoted strings, capitalised tokens,
    hyphenated identifiers, alias-table hits. Ordered, deduped, capped."""
    out: list[str] = []
    seen: set = set()
    candidates = (
        _QUOTED_RE.findall(claim)
        + _CAPS_RE.findall(claim)
        + _HYPHEN_RE.findall(claim)
        + _alias_hits(normalise(claim))
    )
    for cand in candidates:
        norm = normalise(cand)
        if not norm or norm in seen or norm in _STOPWORDS:
            continue
        seen.add(norm)
        out.append(cand)
    return out[:_MAX_SUBJECTS]


def _bucket(artefacts: tuple, store, seen: set, verifying: list, stale: list) -> None:
    """Split one query's ACTIVE artefacts into verify-now vs stale buckets."""
    for art in artefacts:
        marker = (art.get("path"), art.get("subject"))
        if art.get("status") != STATUS_ACTIVE or marker in seen:
            continue
        seen.add(marker)
        target = verifying if index.verify_artefact(art, store=store)["ok"] else stale
        target.append(art)


def _verified_hits(subjects: list, store) -> tuple[list, list, str, dict | None]:
    """(verifying_active, unverified_active, basis, freshness) across subjects."""
    verifying: list = []
    stale: list = []
    basis = "fts"
    freshness: dict | None = None
    seen: set = set()
    for subject in subjects:
        result, sub_basis = index.query_subject_basis(subject, store=store)
        freshness = result.freshness
        before = len(verifying)
        _bucket(result.artefacts, store, seen, verifying, stale)
        if len(verifying) > before and before == 0:
            basis = sub_basis
    return verifying, stale, basis, freshness


def _scope_tuple(scope) -> tuple:
    if scope is None:
        return ()
    if isinstance(scope, str):
        return (scope,) if scope.strip() else ()
    return tuple(s for s in scope if str(s).strip())


def preassert(claim: str, scope=None, store=None) -> PreassertResult:
    """F5 verdict for a (negative-existence) claim. Never basis "semantic"."""
    scope_t = _scope_tuple(scope)
    subjects = extract_subjects(claim)
    if not subjects:
        return PreassertResult(
            verdict=VERDICT_UNVERIFIABLE, claim=claim, scope=scope_t,
            freshness=index.store_freshness(store).to_dict(), basis="exact",
            reason="no checkable subject could be extracted from the claim",
        )
    verifying, stale, basis, freshness = _verified_hits(subjects, store)
    if freshness is None:
        freshness = index.store_freshness(store).to_dict()
    if verifying:
        return PreassertResult(
            verdict=VERDICT_DENY, claim=claim, scope=scope_t,
            evidence=tuple(verifying), freshness=freshness, basis=basis,
            reason="the moat holds ACTIVE artefact(s) for the claim's subject "
                   "that re-verified on disk just now",
        )
    if stale:
        return PreassertResult(
            verdict=VERDICT_UNVERIFIABLE, claim=claim, scope=scope_t,
            evidence=tuple(stale), freshness=freshness, basis=basis,
            reason="moat records exist for the subject but did not re-verify "
                   "on disk; absence is not established either way",
        )
    if scope_t and bool((freshness or {}).get("verified")):
        return PreassertResult(
            verdict=VERDICT_ALLOW, claim=claim, scope=scope_t,
            freshness=freshness, basis=basis,
            reason="honest scoped negative: explicit scope + verified index "
                   "freshness + no exact/alias/fts hit",
        )
    missing = "explicit scope" if not scope_t else "verified index freshness"
    return PreassertResult(
        verdict=VERDICT_UNVERIFIABLE, claim=claim, scope=scope_t,
        freshness=freshness, basis=basis,
        reason=f"negative claim lacks {missing}; say 'not found in <scope>' "
               "instead of asserting non-existence",
    )
