"""Find text the operator can read that talks ABOUT him, or in words he cannot decode.

Pure, no I/O, no side effects. The lexical floor for one class of defect: a hook
writes an instruction to the MODEL on a channel the OPERATOR also reads, and in
doing so refers to him in the third person, or uses a word that only means
something inside GRIP.

THE ORIGINATING INSTANCE (2026-10-01). A Stop hook ended a turn with this, and
the operator read it in his terminal:

    HOLDING for V>> (0s of 1200s) on: ...
    He can answer from any shim — ref 1e40. While you wait, do work that does
    NOT depend on his answer ...

He could not tell what it meant, and named two rules: language aimed at another
instance of the model must not sound like a reference to him, and internal talk
belongs on internal channels. This module is the first rule made checkable.

TWO KINDS
  third_person          he / him / his / himself, an operator signature such as
                        "V>>", "the operator", "the user". In text he can read,
                        each is him being spoken ABOUT rather than spoken TO.
  internal_vocabulary   words with a GRIP-only meaning: "shim" (GRIP's word for
                        an input surface), a short hex reference ("ref 1e40"),
                        and the closer line called a "token".

WHAT IT DELIBERATELY LETS THROUGH
  Second-person text addressed to him ("nothing parked needs a decision from
  you") is the correct register and never fires. "ref" is matched only when a
  hex id containing a digit follows it, so "reply with the ref" and "ref added"
  stay silent. "token" is matched only in GRIP's own compounds ("next-step
  token", "owner token", "closer token"), so "API token" stays silent: he knows
  what an API token is.

THIS IS A FLOOR, NOT A CLASSIFIER
  It reads words, not meaning, so a sentence about a third person (a teammate,
  a child) would fire on "he". That is acceptable where it is used — text a
  hook emits on a channel the operator reads, where "he" has only ever meant
  him — and it is why the scope is stated here rather than discovered later.
  `lib/operator_close_detector.py` is the sibling and the shape this copies.

ONE PASS, ONE PATTERN
  Every rule is an alternative in a single regex with a named group per kind,
  so a scan is one linear pass and the kind is read off `lastgroup`. The
  signature rule is case-sensitive inside an otherwise case-insensitive
  pattern (`(?-i:...)`), so "x>>2" in a code sample is not read as "X>>".
"""

from __future__ import annotations

import re
from typing import List, NamedTuple


class Finding(NamedTuple):
    kind: str
    span: str


# Him, spoken about rather than to.
_THIRD_PERSON = "|".join((
    r"\b(?:he|him|his|himself)\b",
    r"(?<![\w>])(?-i:[A-Z])>>(?!>)",       # an operator signature, e.g. V>>
    r"\bthe\s+(?:operator|user)\b",
))

# Words that only mean something inside GRIP.
_INTERNAL_VOCABULARY = "|".join((
    r"\bshims?\b",
    r"\bref\s+(?=[0-9a-f]*\d)[0-9a-f]{4,}\b",  # a hex id with at least one digit
    r"\b(?:next[- ]step|owner|closer)\s+tokens?\b",
))

_PATTERN = re.compile(
    rf"(?P<third_person>{_THIRD_PERSON})"
    rf"|(?P<internal_vocabulary>{_INTERNAL_VOCABULARY})",
    re.I,
)

_MAX_SPAN = 90


def scan(text: str) -> List[Finding]:
    """Return every hit in `text`, deduplicated by (kind, lowered span)."""
    if not text or not isinstance(text, str):
        return []
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for m in _PATTERN.finditer(text):
        finding = Finding(m.lastgroup or "", m.group(0).strip()[:_MAX_SPAN])
        key = (finding.kind, finding.span.lower())
        if key not in seen:
            seen.add(key)
            out.append(finding)
    return out
