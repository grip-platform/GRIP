#!/usr/bin/env python3
"""text_structure.py — deterministic free text → predicate Structure.

The kernel (sme.py) reasons over Structures: entities, attributes (arity 1)
and relations (arity >= 2). GRIP's live corpora — KONO memories, learning
insights — are free text. This module is the bridge, and it is deliberately
shallow: no NLP dependency, no learned model. A fixed connective lexicon
yields relations, a fixed modifier lexicon yields attributes, a stopword list
plus an identifier pattern yields entities. What it gets wrong it gets wrong
deterministically, and the kernel's own consistency rules (1-1 both ways,
parallel connectivity) do the rest.

Two constraints are inherited from the kernel and worth knowing:

  * Predicate names are unique within a Structure (sme.py keys predicates by
    name), so a second CAUSES claim in one text is named CAUSES_2. Across two
    texts CAUSES pairs with CAUSES and CAUSES_2 with CAUSES_2 — ordinal
    pairing, recorded as a residual in AUDIT.md.
  * Relation arguments are the nearest terms on either side of the connective
    inside one sentence. "X because Y" is stored as CAUSES(Y, X); "X after Y"
    as PRECEDES(Y, X).
"""
from __future__ import annotations

import re
from typing import Iterable

from .sme import PredicateKind, Structure, build_structure

__all__ = ["extract_structure", "extract_structures", "has_relations", "STOPWORDS"]

# phrase (as a token tuple) -> (canonical relation name, arguments reversed?)
_CONNECTIVES: dict[tuple[str, ...], tuple[str, bool]] = {
    ("causes",): ("CAUSES", False), ("cause",): ("CAUSES", False),
    ("caused",): ("CAUSES", False), ("caused", "by"): ("CAUSES", True),
    ("leads", "to"): ("CAUSES", False), ("led", "to"): ("CAUSES", False),
    ("results", "in"): ("CAUSES", False), ("resulted", "in"): ("CAUSES", False),
    ("therefore",): ("CAUSES", False), ("hence",): ("CAUSES", False),
    ("thus",): ("CAUSES", False), ("because",): ("CAUSES", True),
    ("because", "of"): ("CAUSES", True), ("due", "to"): ("CAUSES", True),
    ("prevents",): ("PREVENTS", False), ("prevent",): ("PREVENTS", False),
    ("prevented",): ("PREVENTS", False), ("blocked",): ("PREVENTS", False),
    ("denies",): ("PREVENTS", False), ("denied",): ("PREVENTS", False),
    ("refuses",): ("PREVENTS", False), ("refused",): ("PREVENTS", False),
    ("stops",): ("PREVENTS", False), ("stopped",): ("PREVENTS", False),
    ("kills",): ("PREVENTS", False), ("killed",): ("PREVENTS", False),
    ("suppresses",): ("PREVENTS", False), ("suppressed",): ("PREVENTS", False),
    ("requires",): ("REQUIRES", False), ("needs",): ("REQUIRES", False),
    ("depends", "on"): ("REQUIRES", False), ("depend", "on"): ("REQUIRES", False),
    ("relies", "on"): ("REQUIRES", False), ("rely", "on"): ("REQUIRES", False),
    ("before",): ("PRECEDES", False), ("after",): ("PRECEDES", True),
    ("implies",): ("IMPLIES", False), ("imply",): ("IMPLIES", False),
    ("entails",): ("IMPLIES", False), ("means",): ("IMPLIES", False),
    ("calls",): ("CALLS", False), ("invokes",): ("CALLS", False),
    ("invoked",): ("CALLS", False), ("imports",): ("IMPORTS", False),
    ("imported",): ("IMPORTS", False), ("wraps",): ("WRAPS", False),
    ("wrapped",): ("WRAPS", False), ("reads",): ("READS", False),
    ("writes",): ("WRITES", False), ("wrote",): ("WRITES", False),
    ("returns",): ("RETURNS", False), ("returned",): ("RETURNS", False),
    ("spawns",): ("SPAWNS", False), ("spawned",): ("SPAWNS", False),
    ("triggers",): ("TRIGGERS", False), ("triggered",): ("TRIGGERS", False),
    ("supersedes",): ("SUPERSEDES", False), ("superseded", "by"): ("SUPERSEDES", True),
    ("overrides",): ("OVERRIDES", False), ("->",): ("FLOWS_TO", False),
    ("→",): ("FLOWS_TO", False), ("=>",): ("FLOWS_TO", False),
}
_MAX_CONNECTIVE_SPAN = 2

# modifier word -> canonical attribute name (one-place predicate on a term)
_MODIFIERS: dict[str, str] = {
    "never": "NEVER", "not": "NOT", "no": "NOT", "cannot": "NOT", "without": "NOT",
    "always": "ALWAYS", "only": "ONLY", "silently": "SILENT", "silent": "SILENT",
    "stale": "STALE", "dirty": "DIRTY", "clean": "CLEAN", "empty": "EMPTY",
    "missing": "MISSING", "absent": "MISSING", "broken": "BROKEN", "dead": "DEAD",
    "live": "LIVE", "green": "GREEN", "red": "RED", "fail-open": "FAIL_OPEN",
    "fail-closed": "FAIL_CLOSED", "wrong": "WRONG", "false": "FALSE", "true": "TRUE",
    "slow": "SLOW", "fast": "FAST", "safe": "SAFE", "unsafe": "UNSAFE",
}
_MODIFIER_REACH = 3

STOPWORDS: frozenset[str] = frozenset("""
a an the and or but if else of to in on at by for from with as is are was were
be been being it its this that these those there here he she they them his her
their we our you your i me my do does did done can could should would will may
might must shall have has had having into onto over under up down out off than
too very just also all any some each every both few more most other such own
same what which who whom whose when where why how again further once about
against between through during above below yes then than so
""".split())

_TOKEN_RE = re.compile(
    r"`[^`\n]+`"                                   # backticked span
    r"|[A-Za-z_][\w]*(?:[./:-][A-Za-z_][\w]*)+"     # dotted / path / hyphenated identifier
    r"|[A-Za-z][\w]*"                               # plain word
    r"|->|→|=>"                                     # flow arrows
    r"|[.!?;\n]"                                    # sentence boundaries
)
_BOUNDARIES = frozenset(".!?;\n")
_MAX_ENTITIES = 40


def _tokens(text: str) -> list[str]:
    out = []
    for tok in _TOKEN_RE.findall(text):
        if tok.startswith("`"):
            tok = tok.strip("`").strip()
            if len(tok) < 2:
                continue
        out.append(tok.lower())
    return out


def _sentences(tokens: list[str]) -> list[list[str]]:
    sentences, cur = [], []
    for tok in tokens:
        if tok in _BOUNDARIES:
            if cur:
                sentences.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        sentences.append(cur)
    return sentences


def _is_term(tok: str) -> bool:
    if tok in STOPWORDS or tok in _MODIFIERS or (tok,) in _CONNECTIVES:
        return False
    return len(tok) >= 3 or any(c in tok for c in "_./:-")


def _match_connective(tokens: list[str], i: int) -> tuple[int, str, bool] | None:
    """Longest connective phrase starting at i: (span, canonical, reversed)."""
    for span in range(_MAX_CONNECTIVE_SPAN, 0, -1):
        key = tuple(tokens[i:i + span])
        if len(key) == span and key in _CONNECTIVES:
            canon, rev = _CONNECTIVES[key]
            return span, canon, rev
    return None


def _scan_sentence(tokens: list[str]) -> tuple[list, list, list]:
    """One pass: positioned terms, connective hits, modifier hits."""
    terms, connectives, modifiers = [], [], []
    i = 0
    while i < len(tokens):
        hit = _match_connective(tokens, i)
        if hit is not None:
            span, canon, rev = hit
            connectives.append((i, span, canon, rev))
            i += span
            continue
        tok = tokens[i]
        if tok in _MODIFIERS:
            modifiers.append((i, _MODIFIERS[tok]))
        elif _is_term(tok):
            terms.append((i, tok))
        i += 1
    return terms, connectives, modifiers


def _relations_in(terms: list, connectives: list) -> list[tuple[str, tuple[str, str]]]:
    out = []
    for i, span, canon, rev in connectives:
        left = [t for idx, t in terms if idx < i]
        right = [t for idx, t in terms if idx >= i + span]
        if not left or not right or left[-1] == right[0]:
            continue
        args = (right[0], left[-1]) if rev else (left[-1], right[0])
        out.append((canon, args))
    return out


def _attributes_in(terms: list, modifiers: list) -> list[tuple[str, str]]:
    out = []
    for i, canon in modifiers:
        after = [t for idx, t in terms if i < idx <= i + _MODIFIER_REACH]
        before = [t for idx, t in terms if i - _MODIFIER_REACH <= idx < i]
        if after:
            out.append((canon, after[0]))
        elif before:
            out.append((canon, before[-1]))
    return out


def _unique_names(preds: Iterable[tuple[str, tuple[str, ...]]]) -> list[tuple[str, tuple[str, ...]]]:
    """Kernel contract: predicate names are unique per Structure. Repeats of a
    canonical name get an ordinal suffix; an exact repeat (same name, same
    args) is dropped rather than counted twice."""
    seen_triples: set[tuple[str, tuple[str, ...]]] = set()
    counts: dict[str, int] = {}
    out = []
    for name, args in preds:
        if (name, args) in seen_triples:
            continue
        seen_triples.add((name, args))
        counts[name] = counts.get(name, 0) + 1
        out.append((name if counts[name] == 1 else f"{name}_{counts[name]}", args))
    return out


def extract_structure(name: str, text: str) -> Structure:
    """Free text → Structure. Deterministic; the same text always yields the
    same Structure. Entities are capped at the first 40 distinct terms and
    predicates over dropped terms are dropped with them."""
    entities: list[str] = []
    relations: list[tuple[str, tuple[str, ...]]] = []
    attributes: list[tuple[str, tuple[str, ...]]] = []
    for sentence in _sentences(_tokens(text or "")):
        terms, connectives, modifiers = _scan_sentence(sentence)
        for _, t in terms:
            if t not in entities:
                entities.append(t)
        relations.extend(_relations_in(terms, connectives))
        attributes.extend((canon, (arg,)) for canon, arg in _attributes_in(terms, modifiers))
    kept = set(entities[:_MAX_ENTITIES])
    rel = [(n, a) for n, a in _unique_names(relations) if set(a) <= kept]
    att = [(n, a[0]) for n, a in _unique_names(attributes) if a[0] in kept]
    return build_structure(name, entities[:_MAX_ENTITIES], attributes=att, relations=rel)


def extract_structures(named_texts: Iterable[tuple[str, str]]) -> list[Structure]:
    return [extract_structure(name, text) for name, text in named_texts]


def has_relations(structure: Structure) -> bool:
    return any(p.kind == PredicateKind.RELATION for p in structure.predicates)
