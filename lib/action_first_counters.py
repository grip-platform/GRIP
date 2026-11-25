"""Action-first counters: does a turn start on the action and end on the next step?

GRIP's canonical style says the first line carries the load-bearing fact and the
last line names the next step (rules/canonical-output-style.md). The pre-send
check that the i-have-adhd skill (ayghri/i-have-adhd, MIT, v0.4.1) asks of a
writer says the same in five deletions and one test: delete an announcing first
sentence, a recapping or pleading last sentence, a sidebar, an empty hedge adverb
and an idiom, then read only the first line and the last line and ask whether
they say what to do next and what just happened. This module measures that check
on the turn that was sent. It edits nothing and refuses nothing.

Every counter is a number per turn, or None where the question does not apply:

  opener            1 when the first prose sentence starts with a banned opener
                    ("Great question", "Sure!", "Let me", "I'll", "Looking at your",
                    "To answer your question")
  closer            1 when the last prose sentence starts with a banned closer
                    ("Hope this helps", "Let me know if", "Happy to clarify",
                    "Feel free to ask")
  recap_narration   sentences that narrate the agent's own finished actions in the
                    first person ("I've now done X, Y and Z", "In summary, I ...")
  sidebars          sentences that open a tangent ("By the way", "On a side note",
                    "As an aside", "Incidentally", "One more thing")
  idioms            figurative phrases where a literal action belongs ("circle back",
                    "get the ball rolling", "on the same page", "touch base")
  hedge_adverbs     empty hedge adverbs ("perhaps", "possibly", "maybe", "somewhat",
                    "arguably", "presumably", "seemingly") outside a sentence that
                    carries a real hedge (UNVERIFIED, "it is not established",
                    "may have")
  substantive       1 when the turn carries a RECOMMENDED NEXT STEP closer or at
                    least SUBSTANTIVE_MIN_SENTENCES prose sentences, else 0
  first_line_ok     on a substantive turn: 1 when the first non-heading line is a
                    /tldr line, a fence or an imperative, else 0; None otherwise
  last_line_ok      on a substantive turn: 1 when a closer is present and nothing
                    follows it, in its sentence form or its two-line command form,
                    else 0; None otherwise

Two lists here are deliberately narrow. "Separately" is not a sidebar: it is the
source's own good form for the one deferred item at the end of a turn. No hedge
adverb is a modal verb: lib/ste_counters.py counts the modals for the live STE
trial and reads a fall in them as a dropped caveat, so a word can never sit in
both lists (tests/test_action_first_counters.py pins that).

The phrase tuples are the single source for lib/cos_metric.py edge A, which
imports them. ACTION_FIRST_VERSION changes when any counter's definition changes,
so a reader never compares rows counted two ways. The row never holds text.
"""
from __future__ import annotations

import re

try:
    from . import next_step_router, ste_lint  # type: ignore[attr-defined]  # noqa: PLC0415
except ImportError:
    import next_step_router  # type: ignore[no-redef]  # noqa: PLC0415
    import ste_lint  # type: ignore[no-redef]  # noqa: PLC0415

ACTION_FIRST_VERSION = "1"

#: The floor of prose sentences that makes a turn substantive. It is the STE
#: trial's min_sentences (docs/measurements/2026-10-14-ste-trial-prereg.json), and
#: a test reads that file back so the two cannot drift apart.
SUBSTANTIVE_MIN_SENTENCES = 4

#: Banned openers: the first prose sentence must not start with one of these.
#: The source's list, plus the first-person-future openers GRIP's own metric
#: already banned ("I'll now", "I will now") in their bare form.
OPENERS = ("great question", "sure!", "sure,", "let me", "i'll", "i will", "i'm going to",
           "looking at your", "to answer your question", "first, i'll", "first i'll")
#: Banned closers: the last prose sentence must not start with one of these.
CLOSERS = ("let me know if", "hope this helps", "hope that helps", "happy to clarify",
           "happy to help", "feel free to ask", "feel free to reach out", "anything else?",
           "is there anything else", "don't hesitate to")
#: Sidebars: a tangent opened mid-turn. "Separately" is absent on purpose.
SIDEBARS = ("by the way", "on a side note", "as an aside", "as a side note", "incidentally",
            "one more thing", "while i'm at it", "while we're at it", "also worth mentioning")
#: Figurative phrases where the literal action belongs.
IDIOMS = ("circle back", "the ball rolling", "on the same page", "touch base",
          "low-hanging fruit", "move the needle", "boil the ocean", "hit the ground running",
          "bite the bullet", "down the road", "at the end of the day", "in the weeds",
          "square one", "think outside the box", "the whole nine yards", "ducks in a row",
          "kick the can", "a can of worms", "silver bullet", "the long pole")
#: Empty hedge adverbs. None of these is a modal verb (ste_counters.MODAL).
HEDGE_ADVERBS = ("perhaps", "possibly", "maybe", "somewhat", "arguably", "presumably",
                 "seemingly", "ostensibly", "supposedly", "conceivably")
#: A hedge that carries real uncertainty: a sentence with one keeps its adverbs.
PROTECTED_HEDGE = re.compile(r"\bUNVERIFIED\b|\bit is not established\b|\bnot established\b|"
                             r"\b(?:may|might|could|must|should)\s+(?:not\s+)?have\b", re.I)
#: Narration of the agent's own finished actions, matched at sentence start.
NARRATION = (
    r"i(?:'ve|’ve| have)(?: now| also| just| already)? "
    r"(?:done|completed|finished|made|updated|added|removed|fixed|changed|wired|written|"
    r"created|implemented|run|pushed|merged|checked|verified|tested|shipped)\b",
    r"i(?:'ve|’ve| have) now\b",
    r"in summary\b", r"to summari[sz]e\b", r"to sum up\b",
    r"(?:that|this|which) (?:completes|finishes|wraps up)\b",
    r"so far,? i(?:'ve|’ve| have)\b",
)
#: Words that make a first line an action when they come first: the STE command
#: verbs plus the verbs GRIP's own next-step lines start with.
IMPERATIVE_VERBS = ste_lint.COMMAND_VERBS | frozenset(
    "edit apply review merge rebase fetch compare measure watch verify rerun restart "
    "revert record register rename rewrite update create build deploy export import "
    "list look go cd ls rg git gh python python3 pytest npm node bash sh make".split())

_TLDR = re.compile(r"^\s*/tldr\b", re.I)
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_TLDR_PREFIX = re.compile(r"^\s*/tldr\s*(?:—|--|-|:)?\s*", re.I)
_LEAD = re.compile(r"^[\s\"'“‘(\[*_`>-]+")
_MARKER_LINE = re.compile(r"RECOMMENDED\s+NEXT\s+STEP\s*\(", re.I)


def _phrase_regex(phrases: tuple, anchored: bool) -> re.Pattern:
    """One regex for a tuple of phrases. ``anchored`` binds it to the start of a
    sentence, after markdown emphasis, a list marker or a bracket but never after a
    quotation mark: a quoted phrase at the start of a sentence is a mention of the
    phrase, not a use of it. Unanchored, it matches anywhere. Word guards replace
    \\b, which fails after "!" or "?"."""
    body = "|".join(re.escape(p) for p in phrases)
    head = r"^[\s(\[*_`>-]*" if anchored else r"(?<![\w'])"
    return re.compile(f"{head}(?:{body})(?![\\w-])", re.I)


_OPENER = _phrase_regex(OPENERS, anchored=True)
_CLOSER = _phrase_regex(CLOSERS, anchored=True)
_SIDEBAR = _phrase_regex(SIDEBARS, anchored=False)
_IDIOM = _phrase_regex(IDIOMS, anchored=False)
_HEDGE = _phrase_regex(HEDGE_ADVERBS, anchored=False)
_NARRATION = re.compile(r"^[\s\"'“‘(\[*_`>-]*(?:" + "|".join(NARRATION) + ")", re.I)


def _prose_sentences(text: str) -> list:
    """Every prose sentence of the turn, in order. Code, tables and headings are
    set aside by ste_lint.blocks_of."""
    return [sentence for _, block in ste_lint.blocks_of(text) for sentence in ste_lint.sentences_of(block)]


def _without_tldr(sentence: str) -> str:
    return _TLDR_PREFIX.sub("", sentence, count=1)


def _opening_sentences(text: str) -> list:
    """The sentences an opener can start: the first sentence of the first prose
    block, and, when that block is the /tldr line, the first sentence of the block
    after it. The /tldr line carries the fact; the recap under it is where a
    "Great question!" lands in GRIP's own shape."""
    blocks = [block for _, block in ste_lint.blocks_of(text)]
    firsts = [ste_lint.sentences_of(block)[:1] for block in blocks[:2]]
    firsts = [first[0] for first in firsts if first]
    if not firsts:
        return []
    if _TLDR.match(firsts[0]):
        return [_without_tldr(firsts[0]), *firsts[1:2]]
    return firsts[:1]


def opener(text: str) -> int:
    """1 when an opening sentence starts with a banned opener."""
    return int(any(_OPENER.search(sentence) for sentence in _opening_sentences(text)))


def _last_content_line(text: str):
    """The last line that is not blank, a heading or a fence marker, or None."""
    return next((line for line in reversed(text.splitlines())
                 if line.strip() and not _HEADING.match(line) and not _FENCE.match(line)), None)


def closer(text: str) -> int:
    """1 when the last prose sentence, or the last sentence of the last line, starts
    with a banned closer. The last line is read on its own because a closer written
    under the next-step marker ("... (mine) -- x\\nHope this helps.") shares the
    marker's paragraph, and so its sentence, in ste_lint.blocks_of."""
    sentences = _prose_sentences(text)
    last_line = _last_content_line(text)
    last_of_line = ste_lint.sentences_of(last_line)[-1:] if last_line else []
    return int(any(_CLOSER.search(sentence) for sentence in [*sentences[-1:], *last_of_line]))


def recap_narration(text: str) -> int:
    """Sentences that narrate the agent's own finished actions."""
    return sum(1 for sentence in _prose_sentences(text) if _NARRATION.search(sentence))


def sidebars(text: str) -> int:
    """Sentences that open a tangent."""
    return sum(1 for sentence in _prose_sentences(text) if _SIDEBAR.search(sentence))


def idioms(text: str) -> int:
    """Figurative phrases in the prose."""
    return sum(len(_IDIOM.findall(sentence)) for sentence in _prose_sentences(text))


def hedge_adverbs(text: str) -> int:
    """Empty hedge adverbs, outside sentences that carry a real hedge."""
    return sum(len(_HEDGE.findall(sentence)) for sentence in _prose_sentences(text)
               if not PROTECTED_HEDGE.search(sentence))


def is_substantive(text: str) -> bool:
    """A turn is substantive when it carries a next-step closer or at least
    SUBSTANTIVE_MIN_SENTENCES prose sentences."""
    if next_step_router.parse(text).owner is not next_step_router.Owner.ABSENT:
        return True
    return len(_prose_sentences(text)) >= SUBSTANTIVE_MIN_SENTENCES


def _first_content_line(text: str):
    """The first line that is not blank and not a heading, or None."""
    return next((line for line in text.splitlines() if line.strip() and not _HEADING.match(line)), None)


def first_line_ok(text: str):
    """On a substantive turn, whether the first non-heading line is a /tldr line, a
    fence or an imperative. None on a turn that is not substantive."""
    if not is_substantive(text):
        return None
    line = _first_content_line(text)
    if line is None:
        return False
    if _TLDR.match(line) or _FENCE.match(line):
        return True
    words = ste_lint.WORD.findall(_LEAD.sub("", line).lower())
    return bool(words) and words[0] in IMPERATIVE_VERBS


def _closer_tail(text: str) -> list:
    """The non-blank lines after the closer's marker line, fence markers removed."""
    lines = text.splitlines()
    marker_at = max((i for i, line in enumerate(lines) if _MARKER_LINE.search(line)), default=None)
    if marker_at is None:
        return []
    after = [line.strip() for line in lines[marker_at + 1:]]
    return [line for line in after if line and not _FENCE.match(line)]


def last_line_ok(text: str):
    """On a substantive turn, whether a next-step closer is present and nothing
    follows it: in its sentence form the marker line is last, and in its two-line
    command form one command line follows the marker and nothing follows that.
    None on a turn that is not substantive."""
    if not is_substantive(text):
        return None
    step = next_step_router.parse(text)
    if step.owner is next_step_router.Owner.ABSENT:
        return False
    rest = _closer_tail(text)
    # The router's marker regex lets whitespace span a line break ("RECOMMENDED NEXT\nSTEP
    # (mine)"), so a closer can parse while no single line carries the marker. That
    # closer is not on one line, so the last line is not ok; it is never a fault
    # (AGORA on GRIP#8561, R0-tested: a StopIteration here cost the operator the turn's line).
    marker_line = next((line for line in reversed(text.splitlines()) if _MARKER_LINE.search(line)), None)
    if marker_line is None:
        return False
    sentence_form = re.search(r"\)\s*(?:—|--?|:)\s*\S", marker_line) is not None
    return len(rest) == 0 if sentence_form else len(rest) == 1


def _flag(value):
    return None if value is None else int(bool(value))


def count(text) -> dict:
    """The counters of one turn. Numbers or None only: nothing of ``text`` is kept.
    A value that is not a string counts as an empty turn."""
    if not isinstance(text, str):
        text = ""
    return {
        "opener": opener(text),
        "closer": closer(text),
        "recap_narration": recap_narration(text),
        "sidebars": sidebars(text),
        "idioms": idioms(text),
        "hedge_adverbs": hedge_adverbs(text),
        "substantive": int(is_substantive(text)),
        "first_line_ok": _flag(first_line_ok(text)),
        "last_line_ok": _flag(last_line_ok(text)),
    }
