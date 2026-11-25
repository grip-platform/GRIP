#!/usr/bin/env python3
"""COS four-sided Goodhart metric (GRIP #2868 W3).

Implements the frozen W0 §2 contract (docs/cos-w0-constraint-lock.md):
an output is REJECTED if ANY single edge fails. Each edge is a
mechanically-computable proxy. This is the *contract detector* — it does
NOT transform text (that is W2's compose shim). Goodhart-proof: each
edge can actually fail, so a green result means something.

Reuses existing surfaces (Rule 21 compose-not-rebuild):
- Edge A banned-phrase set ≈ skills/concise-communication preamble/
  postamble regex.
- Edge C delegates the structural verdict to the caller's SMT function
  (the gentner-enforcer agent / lib.backlog_gates.gentner_connection_
  gate); this module never re-implements Gentner.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

try:
    from . import action_first_counters as _af  # type: ignore[attr-defined]  # noqa: PLC0415
except ImportError:
    import action_first_counters as _af  # type: ignore[no-redef]  # noqa: PLC0415

# Edge A — preamble/postamble/hedging. The W0 §2 table defines edge A as the
# banned-phrase set, so the set may widen inside the frozen contract. Since
# 2026-10-18 the openers, closers and recap narration of the i-have-adhd pre-send
# check (ayghri/i-have-adhd, MIT, v0.4.1) join it, and the phrase tuples live once
# in lib/action_first_counters.py, which the Stop hook also reads. This module
# keeps the legacy phrases of skills/concise-communication beside them.
_LEGACY_FILLER = (
    "I'll now", "I will now", "I'm going to", "first, I'll", "first I'll",
    "in summary", "to summarize", "to sum up", "I have completed", "I have now completed",
    "I've finished", "I've now finished", "successfully completed",
    "as you can see", "it's worth noting that", "please note that",
    "basically", "essentially", "in order to", "at this point in time",
)
#: The two bare first-person-future openers. Anywhere in a text they are ordinary
#: ("I'll watch the PR"); only as the first sentence are they an opener.
_FIRST_SENTENCE_ONLY = ("i'll", "i will")
#: Every phrase edge A refuses anywhere in the text, legacy and shared, in order.
EDGE_A_ANYWHERE = tuple(dict.fromkeys((
    *_LEGACY_FILLER,
    *(phrase for phrase in _af.OPENERS if phrase not in _FIRST_SENTENCE_ONLY),
    *_af.CLOSERS,
)))


def _phrases(phrases) -> str:
    return "|".join(re.escape(phrase) for phrase in phrases)


# Word guards rather than \b: a phrase that ends in "!" or "?" has no word boundary.
_FILLER = re.compile(r"(?<![\w'])(?:" + _phrases(EDGE_A_ANYWHERE) + r")(?![\w-])", re.IGNORECASE)
#: Recap narration, at the start of a sentence: lib/action_first_counters.NARRATION.
_NARRATION = re.compile(r"(?:^|(?<=[.!?])\s+)[\s(\[*_`>-]*(?:" + "|".join(_af.NARRATION) + ")",
                        re.IGNORECASE | re.MULTILINE)
_OPENER_FIRST = re.compile(r"^[\s(\[*_`>-]*(?:" + _phrases(_FIRST_SENTENCE_ONLY) + r")(?![\w-])", re.IGNORECASE)
_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s")
_TLDR_PREFIX = re.compile(r"^\s*/tldr\s*(?:—|--|-|:)?\s*", re.IGNORECASE)


def _first_content_line(text: str) -> str:
    """The first line that is not blank and not a heading, with a /tldr marker removed."""
    for line in (text or "").splitlines():
        if line.strip() and not _HEADING_LINE.match(line):
            return _TLDR_PREFIX.sub("", line, count=1)
    return ""


def _opens_with_first_person_future(text: str) -> bool:
    """True when the first sentence after any leading heading starts with a bare
    "I'll" or "I will". A later "I'll watch the PR" is not an opener."""
    return _OPENER_FIRST.search(_first_content_line(text)) is not None


def _edge_a_phrase_present(text: str) -> bool:
    text = text or ""
    return bool(_FILLER.search(text) or _NARRATION.search(text) or _opens_with_first_person_future(text))

# Technical-token shapes (edge B): paths, #IDs, symbols::sym, CONSTS,
# dotted.calls, flags, numbers-with-unit. Deliberately broad — a dropped
# fact is worse than an over-counted token.
_TECH = re.compile(
    r"(?:[\w./-]+\.[a-zA-Z]{1,6}\b)"        # file.ext / path/file.py
    r"|(?:#\d+)"                              # #2868
    r"|(?:\b\w+::\w+)"                        # mod::sym
    r"|(?:\b[A-Z][A-Z0-9_]{2,}\b)"           # CONSTANT
    r"|(?:\b\w+\.\w+\([^)]*\))"               # obj.method(...)
    r"|(?:(?<![\w-])--?[a-zA-Z][\w-]+)"       # --flag / -f, never the tail of fail-closed
    r"|(?:\b\d+(?:\.\d+)?(?:%|ms|s|kb|mb|x)\b)"  # 35% 40ms 2x
)


def _tech_tokens(text: str) -> set[str]:
    return set(_TECH.findall(text or ""))


@dataclass
class MetricResult:
    """Per-edge pass/fail. `ok` is the AND of all four (W0 §2: any fail
    rejects)."""

    edge_a_concise: bool = True
    edge_b_lossless: bool = True
    edge_c_smt: bool = True
    edge_d_reasoning: bool = True
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.edge_a_concise
            and self.edge_b_lossless
            and self.edge_c_smt
            and self.edge_d_reasoning
        )


def _is_nontrivial(task: str) -> bool:
    """W0 §2 edge-D definition: ≥2 steps, OR ≥2 cross-domain tech
    tokens, OR a why/trade-off/decision ask. Pure direct commands are
    trivial."""
    t = (task or "").lower()
    if re.search(r"\b(why|trade-?off|decide|decision|compare|versus|vs\.?)\b", t):
        return True
    if re.search(r"\b(then|after that|and then|next,|step \d)\b", t):
        return True
    if len(_tech_tokens(task)) >= 2:
        return True
    return False


def evaluate(
    styled: str,
    *,
    source: str = "",
    task: str = "",
    analogy_span: Optional[str] = None,
    analogy_similarity: float = 1.0,
    reasoning_present: bool = True,
    token_ceiling: Optional[int] = None,
    smt_fn: Optional[Callable[..., bool]] = None,
) -> MetricResult:
    """Score the four W0 §2 edges. ANY edge failing → result.ok False.

    - A: filler/preamble/postamble regex hit OR token count over ceiling.
    - B: a technical token in `source` absent from `styled`.
    - C: an analogy span the SMT function rejects as surface-level.
    - D: task non-trivial AND no reasoning trace present.
    Defaults are the *passing* values so an unscored edge never falsely
    rejects (R0 degradation: an uncomputed edge degrades, never asserts).
    """
    r = MetricResult()

    # Edge A — verbose.
    if _edge_a_phrase_present(styled):
        r.edge_a_concise = False
        r.failures.append("A: filler/preamble/postamble phrase present")
    elif token_ceiling is not None and len((styled or "").split()) > token_ceiling:
        r.edge_a_concise = False
        r.failures.append(
            f"A: {len(styled.split())} words > ceiling {token_ceiling}"
        )

    # Edge B — fact dropped.
    if source:
        missing = _tech_tokens(source) - _tech_tokens(styled)
        if missing:
            r.edge_b_lossless = False
            r.failures.append(
                f"B: technical tokens dropped: {sorted(missing)}"
            )

    # Edge C — surface analogy (delegated; never re-implemented here).
    if analogy_span and smt_fn is not None:
        if not bool(smt_fn(styled, analogy_span, analogy_similarity)):
            r.edge_c_smt = False
            r.failures.append("C: analogy is surface-level (SMT reject)")

    # Edge D — reasoning omitted.
    if task and _is_nontrivial(task) and not reasoning_present:
        r.edge_d_reasoning = False
        r.failures.append(
            "D: non-trivial task with no reasoning trace"
        )

    return r
