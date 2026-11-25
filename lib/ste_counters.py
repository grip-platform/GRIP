"""Hidden counters for the STE trial: numbers a writer cannot see and a score cannot game.

The STE score (lib/ste_lint.py) rewards short sentences and no semicolons. A reply
can earn that score by dropping articles, by cutting a condition or a reason, or
by moving its prose into a table, and the 2026-10-10 plan measured all three
scoring a perfect 1.00 (plans/ULTRAPLAN-2026-10-10-ste-improver-loop.md, D2). The
counters here record what those routes change. The Stop hook writes them into
each log row (lib/ste_hook.py); the arm report (scripts/ste-arm-report.py) reads
them as check C of the ratchet: no counter of the ON arm may fall further than its
own noise floor. The model never reads a counter. The row never holds text.

Every counter is a number per turn:

  words                prose words, after code, tables and headings are set aside
  all_words            every whitespace token of the turn, prose or not
  prose_share          words / all_words (1.0 when the turn has no words)
  sentences            prose sentences
  words_per_sentence   words / sentences (0.0 with no sentence)
  determiners_per_100  a, an, the, this, that ... per 100 prose words
  technical            prose tokens that are technical names (paths, ids, versions)
  numbers              bare numbers in the prose
  connectives          because, so, if, unless, therefore, although, until, then
  negations            not, no, never, none, nothing, nobody, without, neither, nor,
                       cannot, and the n't contractions
  modals               may, might, could, must, should, can, will, would, shall

COUNTERS_VERSION changes when any counter's definition changes, so a report never
compares rows counted two ways.
"""
from __future__ import annotations

import re

try:
    from . import ste_lint  # type: ignore[attr-defined]  # noqa: PLC0415
except ImportError:
    import ste_lint  # type: ignore[no-redef]  # noqa: PLC0415

COUNTERS_VERSION = "1"

#: The counters a lower value makes worse: a dropped article, fact, reason, caveat
#: or hedge, or prose moved into a table. words_per_sentence is the mechanism the
#: trial expects to fall, so it is recorded but never judged.
LOWER_IS_WORSE = ("determiners_per_100", "technical", "numbers", "connectives", "negations",
                  "modals", "prose_share")

CONNECTIVE = re.compile(r"\b(?:because|so|if|unless|therefore|although|until|then)\b", re.I)
NEGATION = re.compile(r"\b(?:not|no|never|none|nothing|nobody|without|neither|nor|cannot)\b|n[’']t\b", re.I)
MODAL = re.compile(r"\b(?:may|might|could|must|should|can|will|would|shall)\b", re.I)
NUMBER = re.compile(r"(?<![\w.])[+-]?\d[\d,]*(?:\.\d+)?%?(?![\w])")


def _prose_blocks(text: str) -> list[str]:
    return [block for _, block in ste_lint.blocks_of(text)]


def _per_100(count: int, words: int) -> float:
    return round(100.0 * count / words, 2) if words else 0.0


def count(text: str) -> dict:
    """The counters of one turn. Numbers only: nothing of ``text`` is kept."""
    blocks = _prose_blocks(text)
    prose = "\n".join(blocks)
    tokens = ste_lint.TOKEN.findall(prose)
    words = len(ste_lint.WORD.findall(prose))
    all_words = len(ste_lint.TOKEN.findall(text))
    sentences = sum(len(ste_lint.sentences_of(block)) for block in blocks)
    return {
        "words": words,
        "all_words": all_words,
        "prose_share": round(words / all_words, 4) if all_words else 1.0,
        "sentences": sentences,
        "words_per_sentence": round(words / sentences, 2) if sentences else 0.0,
        "determiners_per_100": _per_100(len(ste_lint.DETERMINER.findall(prose)), words),
        "technical": sum(1 for token in tokens if ste_lint.is_technical(token)),
        "numbers": len(NUMBER.findall(prose)),
        "connectives": len(CONNECTIVE.findall(prose)),
        "negations": len(NEGATION.findall(prose)),
        "modals": len(MODAL.findall(prose)),
    }
