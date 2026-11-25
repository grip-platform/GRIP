"""Parse and normalise the LEXICON, KERN's decoding key (rules/00-lexicon.md).

File shape, strictly parsed so a malformed LEXICON fails loudly:

    LEXICON <version> <preamble text ...>
    <more preamble lines, optional, before the first entry>
    <Code> = <meaning>
    <Code> = <meaning>
    // a comment line, never a rule

An entry is VOCABULARY when its code is two or more characters and starts with
a letter or ``@`` ("Canon", "OM", "@Gate", "P!"). Vocabulary is what the codec
encodes and expands, what the Hamming guard compares, and what must pay its
way. Any other entry ("!", "->", "K") is GRAMMAR: it documents the notation's
syntax and is never substituted into text, because substituting a lone symbol
or letter would rewrite ordinary punctuation.

The LEXICON is self-describing and sits at its own fixed point:
:func:`normalise` rewrites each definition using only the terms defined BEFORE
it, so a term can be read top to bottom with nothing forward-referenced. A
LEXICON is normalised exactly when ``normalise(text) == text``.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

from ._util import duplicates
from .codec import Codec, prose_of

HEADER_RE = re.compile(r"^LEXICON (?P<version>\S+)")
ENTRY_RE = re.compile(r"^(?P<code>\S+) = (?P<meaning>\S.*?)\s*$")
VOCAB_RE = re.compile(r"^[A-Za-z@][A-Za-z0-9!_-]+$")
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


class LexiconError(ValueError):
    """The LEXICON does not parse, so nothing that depends on it can be trusted."""


@dataclass(frozen=True)
class Entry:
    code: str
    meaning: str
    line_no: int  # 0-based index into the file's lines

    @property
    def is_vocabulary(self) -> bool:
        return bool(VOCAB_RE.match(self.code))


@dataclass(frozen=True)
class Lexicon:
    version: str
    entries: tuple[Entry, ...]
    lines: tuple[str, ...]

    @property
    def vocabulary(self) -> tuple[Entry, ...]:
        return tuple(e for e in self.entries if e.is_vocabulary)

    @property
    def codes(self) -> list[str]:
        return [e.code for e in self.vocabulary]

    def pairs(self) -> list[tuple[str, str]]:
        return [(e.code, e.meaning) for e in self.vocabulary]

    def codec(self) -> Codec:
        return Codec(self.pairs())


def _body_start(text: str) -> int:
    match = _FRONTMATTER_RE.match(text)
    return match.group(0).count("\n") if match else 0


def _classify(line: str, seen_entry: bool, n: int) -> str:
    """'skip', 'entry' or 'preamble' for one line after the header."""
    stripped = line.strip()
    if not stripped or stripped.startswith("//"):
        return "skip"
    if ENTRY_RE.match(line):
        return "entry"
    if seen_entry:
        raise LexiconError(f"line {n + 1}: after the first entry every line must be "
                           f"'Code = meaning', blank or a // comment: {line!r}")
    return "preamble"


def _header(lines: list[str], start: int) -> tuple[int, str]:
    first = next((n for n in range(start, len(lines)) if lines[n].strip()), None)
    if first is None:
        raise LexiconError("empty LEXICON: no header line")
    match = HEADER_RE.match(lines[first])
    if not match:
        raise LexiconError(f"line {first + 1}: expected 'LEXICON <version> ...', "
                           f"got {lines[first]!r}")
    return first, match.group("version")


def parse(text: str) -> Lexicon:
    """Parse a LEXICON strictly; duplicates and stray lines raise LexiconError."""
    lines = text.split("\n")
    header_at, version = _header(lines, _body_start(text))
    entries: list[Entry] = []
    for n in range(header_at + 1, len(lines)):
        if _classify(lines[n], bool(entries), n) == "entry":
            match = ENTRY_RE.match(lines[n])
            entries.append(Entry(match.group("code"), match.group("meaning"), n))
    lex = Lexicon(version=version, entries=tuple(entries), lines=tuple(lines))
    _refuse_duplicates(lex)
    return lex


def _refuse_duplicates(lex: Lexicon) -> None:
    dupes = duplicates(e.code for e in lex.entries)
    if dupes:
        raise LexiconError(f"a code is defined twice: {dupes}")
    dupes = duplicates(e.meaning for e in lex.vocabulary)
    if dupes:
        raise LexiconError(f"two codes share one meaning, so encoding is ambiguous: {dupes}")


def normalise(text: str) -> str:
    """The LEXICON's fold: each meaning encoded with the terms defined before it.

    Preamble, comments, blank lines and grammar entries are kept verbatim; only
    vocabulary definitions are rewritten, as ``Code = meaning`` with single
    spaces. Applying it twice changes nothing.
    """
    lex = parse(text)
    lines = list(lex.lines)
    prior: list[tuple[str, str]] = []
    for entry in lex.entries:
        meaning = entry.meaning
        if entry.is_vocabulary:
            meaning = Codec(prior).encode(meaning) if prior else meaning
            prior.append((entry.code, entry.meaning))
        lines[entry.line_no] = f"{entry.code} = {meaning}"
    return "\n".join(lines)


def _any_of(codes: list[str]) -> re.Pattern[str] | None:
    if not codes:
        return None
    alternation = "|".join(re.escape(c) for c in sorted(codes, key=len, reverse=True))
    return re.compile(rf"(?<![\w])(?:{alternation})(?![\w])")


def forward_references(lex: Lexicon) -> list[tuple[str, str]]:
    """(entry, term) pairs where a definition uses a term defined at or after it."""
    vocab = lex.vocabulary
    out = []
    for i, entry in enumerate(vocab):
        pattern = _any_of([e.code for e in vocab[i:]])
        found = sorted(set(pattern.findall(prose_of(entry.meaning)))) if pattern else []
        out.extend((entry.code, term) for term in found)
    return out


def within_one_edit(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` are equal or one insertion, deletion or substitution apart."""
    if abs(len(a) - len(b)) > 1:
        return False
    short, long_ = sorted((a, b), key=len)
    i = next((k for k, (x, y) in enumerate(zip(short, long_)) if x != y), len(short))
    if len(short) == len(long_):
        return short[i + 1:] == long_[i + 1:]
    return short[i:] == long_[i + 1:]


def too_close(lex: Lexicon) -> list[tuple[str, str]]:
    """Code pairs one typo apart, so a slip turns one valid code into another."""
    return [(a, b) for a, b in itertools.combinations(lex.codes, 2) if within_one_edit(a, b)]
