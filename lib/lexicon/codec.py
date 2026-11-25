"""Encode and expand between KERN's defined terms and their meanings.

``encode`` replaces each defined meaning with its term ("origin/main" becomes
"OM"); ``expand`` does the reverse and is the human-facing Rosetta view, so a
reader who has never seen the LEXICON reads exactly what the model reads, in
English. Three rules keep the mapping honest:

  * Text inside backticks is never touched. Commands are copied by models and
    humans literally, so a term inside one would be run as a term.
  * Matching is case-sensitive and word-bounded. "Canon" the defined term is
    not "canon" the English word, and "OM" never matches inside "ROM".
  * Both directions repeat until nothing changes. A meaning may use an earlier
    term ("Done = on OM and Canon read"), so expansion is recursive, and a
    definition that refers back to itself is refused rather than looped on.

Because ``encode`` stops only at a pass that changes nothing, applying it to
its own output is a no-op: ``encode(encode(x)) == encode(x)`` by construction.
:class:`KernCodecDomain` exposes that fold to lib/dogfood_kernel.py, which
verifies it by double application rather than by trusting this paragraph.
"""
from __future__ import annotations

import re
from typing import Callable, Sequence

from ._util import duplicates

_SPAN_RE = re.compile(r"(`[^`\n]*`)")


class CodecError(ValueError):
    """The vocabulary is malformed, or a rewrite never reaches a fixed point."""


def word_pattern(phrase: str) -> re.Pattern[str]:
    """``phrase`` as a whole word or phrase, never as part of a longer word."""
    return re.compile(rf"(?<![\w]){re.escape(phrase)}(?![\w])")


def segments(line: str) -> list[tuple[bool, str]]:
    """Split one line into (is_command, text) parts around backtick spans.

    A line with an unmatched backtick is returned whole as a command: when it
    is unclear where a command ends, the safe reading is that it has not.
    """
    if line.count("`") % 2:
        return [(True, line)]
    return [(part.startswith("`"), part) for part in _SPAN_RE.split(line) if part]


def map_prose(text: str, fn: Callable[[str], str]) -> str:
    """Apply ``fn`` to every prose segment of ``text``, leaving commands verbatim."""
    out = []
    for line in text.split("\n"):
        out.append("".join(part if is_cmd else fn(part) for is_cmd, part in segments(line)))
    return "\n".join(out)


def prose_of(text: str) -> str:
    """Only the prose of ``text``: every command span removed, lines kept."""
    return "\n".join(
        "".join(part for is_cmd, part in segments(line) if not is_cmd)
        for line in text.split("\n"))


def _validate(pairs: Sequence[tuple[str, str]]) -> None:
    if any(not c or not m for c, m in pairs):
        raise CodecError("every term needs a non-empty code and meaning")
    for label, items in (("code", [c for c, _ in pairs]), ("meaning", [m for _, m in pairs])):
        dupes = duplicates(items)
        if dupes:
            raise CodecError(f"duplicate {label}(s): {dupes}")
    same = sorted(c for c, m in pairs if c == m)
    if same:
        raise CodecError(f"a code may not equal its own meaning: {same}")


class Codec:
    """A one-to-one vocabulary map between defined terms and their meanings."""

    def __init__(self, pairs: Sequence[tuple[str, str]]):
        _validate(pairs)
        self.pairs = tuple(pairs)
        by_meaning = sorted(self.pairs, key=lambda p: (-len(p[1]), p[1]))
        by_code = sorted(self.pairs, key=lambda p: (-len(p[0]), p[0]))
        self._enc = [(word_pattern(m), c) for c, m in by_meaning]
        self._dec = [(word_pattern(c), m) for c, m in by_code]
        self._limit = len(self.pairs) + 2

    @staticmethod
    def _apply(rules: list, prose: str) -> str:
        for pattern, replacement in rules:
            prose = pattern.sub(lambda _m, r=replacement: r, prose)
        return prose

    def _settle(self, rules: list, text: str, verb: str) -> str:
        for _ in range(self._limit):
            nxt = map_prose(text, lambda p: self._apply(rules, p))
            if nxt == text:
                return text
            text = nxt
        raise CodecError(f"{verb} did not settle in {self._limit} passes: "
                         "a definition refers to itself, directly or in a cycle")

    def encode(self, text: str) -> str:
        """Replace every defined meaning with its term, to a fixed point."""
        return self._settle(self._enc, text, "encode")

    def expand(self, text: str) -> str:
        """Replace every defined term with its meaning, recursively (Rosetta)."""
        return self._settle(self._dec, text, "expand")

    def residual(self, text: str) -> int:
        """How many defined meanings still appear un-encoded in the prose.

        Zero exactly when :meth:`encode` would change nothing, which is the
        agreement lib/dogfood_kernel.py checks between the fold and its metric.
        """
        prose = prose_of(text)
        return sum(len(pattern.findall(prose)) for pattern, _ in self._enc)

    def uses(self, code: str, text: str) -> int:
        """Occurrences of ``code`` in the prose of ``text`` (commands excluded)."""
        return len(word_pattern(code).findall(prose_of(text)))

    def codes_in_commands(self, text: str) -> list[str]:
        """Terms that appear inside a backtick span, where they would run literally."""
        commands = " ".join(part for line in text.split("\n")
                            for is_cmd, part in segments(line) if is_cmd)
        return sorted(c for c, _ in self.pairs if word_pattern(c).search(commands))


class KernCodecDomain:
    """The codec as a lib/dogfood_kernel.py domain: fold = encode, metric = residual."""

    def __init__(self, codec: Codec):
        self.codec = codec

    def fold(self, x: str) -> str:
        return self.codec.encode(x)

    def residual(self, x: str) -> int:
        return self.codec.residual(x)

    @property
    def zero_residual(self) -> int:
        return 0
