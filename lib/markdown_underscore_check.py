"""The one check behind MD-US, GRIP's own rule: an underscore-joined identifier
outside code, caught because some Markdown viewers render it as italics.

CommonMark (and GitHub's renderer) never turns `per_person_sharing` into
italics: the spec says an underscore cannot open emphasis inside a word.
The operator's own viewer does not follow that rule, so a snake_case
identifier, a file name such as `build_handcheck.py`, or a key such as
`per_child`, written bare in prose renders with its middle word in italics.
markdownlint has no rule number for this either -- it is a GRIP-specific
check, not MD040/MD036/the blank-line family in patterns.json. GRIP calls it
MD-US ("underscore") so it has a name alongside the numbered rules it sits
beside, with no claim that markdownlint defines it.

THE RULE: a run of word characters joined by one or more literal
underscores, outside any protected region, optionally followed by a dotted
extension (so a file name is wrapped whole, extension included). THE FIX:
wrap the whole match in backticks. Applying the fix twice changes nothing
the second time -- a wrapped identifier is inline code on the next pass, so
it is never matched again. --fix preserves the file's own line endings
(CRLF stays CRLF): the read and the write both disable newline translation.

NEVER TOUCHED, because each is a protected region computed before any match
is kept: a fenced code block, also inside a block quote or a list item (the
closer is the opener's marker character, at least as long, followed by
nothing but whitespace -- a line such as "```not-a-close" inside an open
fence is still content, exactly as CommonMark reads it); an inline code span
of any backtick length, closed by a run of the same length, which may cross
line endings but never a blank line, because a blank line ends the paragraph
before inline parsing ever sees it; a link or
image target and its title, the `(...)` right after `]`, with its
parentheses balanced (`[x](docs/(v1)/foo_bar.md)` protects the whole
destination, not just up to the first `)`); an autolink or an HTML tag,
`<...>` -- opened only by `<` followed by a tag-name letter, `/` or `!` (so
`a < b` stays ordinary prose), and like a code span allowed one line ending
but never a blank line; a bare URL of any scheme, or a www. address; a
reference-style link definition line;
every reference label -- the `[label]` of `[text][label]`, and any `[label]`
naming a label the document defines, matched case-insensitively -- because a
label in backticks no longer matches its definition and the link breaks;
and the very first YAML front-matter block.

NEVER TOUCHED EITHER, by the shape of the matching rule rather than by a
separate exclusion: a candidate that touches an escaped underscore
(`per\\_person`, or `foo\\_bar_baz` where only ONE of its two underscores is
escaped) is rejected WHOLE -- the escape is not salvaged from, the entire
contiguous run is left bare. `_text_` and `__text__` emphasis markers never
qualify either, because the rule's first character class excludes
underscore, so no match can begin on one, and a lone closing underscore (no
word character after it) cannot complete the required word-underscore-word
shape.

KNOWN LIMITS: a fence's closer is not required to carry the same quote or
list prefix as its opener (reading too much as code only protects more);
only a single leading backslash is read as an escape; a word that begins
with an underscore (`__init__`) is never a candidate.

usage: grip-py lib/markdown_underscore_check.py [--fix] FILE [FILE ...]
"""
from __future__ import annotations

import bisect
import re
import sys
from dataclasses import dataclass

#: One line ending, bare LF or CRLF -- used everywhere a protected region is
#: allowed to cross a single line break but never a blank line.
_EOL = r"\r?\n"
#: A fence-marker line: any block-quote markers ('>') and indentation, an
#: optional list-item marker, then 3+ backticks or tildes -- so a fence inside
#: a quote or a list item is seen too. Matches an OPENER (any trailing text,
#: e.g. an info string, is fine) and is reused, with the whitespace-only check
#: below, to find the CLOSER. Reading too much as a fence only protects more.
FENCE_LINE = re.compile(r"^(?:[ \t]*>)*[ \t]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)?(`{3,}|~{3,})")
#: An inline code span: an opening run of backticks closed by a run of the
#: SAME length (so ``a `b_c` d`` is one span), crossing line endings but never
#: a blank line, because a blank line ends the paragraph first.
INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(?:(?!" + _EOL + _EOL + r")[\s\S])+?(?<!`)\1(?!`)")
#: "](" -- the start of a link or image target; the balanced close is found
#: by `_link_target_end`, not by this pattern alone (CommonMark destinations
#: may contain balanced parentheses).
LINK_TARGET_OPEN = re.compile(r"\]\(")
#: An autolink or an HTML tag: `<` then a tag-name letter, `/` or `!` (never
#: a bare comparison like `a < b`), through to its `>`, allowed one line
#: ending but never a blank line.
ANGLE = re.compile(r"<[A-Za-z!/](?:[^<>\r\n]|" + _EOL + r"(?!" + _EOL + r"))*>")
#: A bare URL with any scheme (https, ftp, ...), or a scheme-less www. address.
BARE_URL = re.compile(r"(?:\b[A-Za-z][A-Za-z0-9+.-]*://|\bwww\.)\S+")
#: A reference-style link definition line: `[label]: target "title"`.
REFERENCE_DEF = re.compile(r'^[ \t]{0,3}\[[^\]\n]+\]:.*$', re.M)
#: The label of a definition line, captured, so every use of it is protected.
REFERENCE_DEF_LABEL = re.compile(r"^[ \t]{0,3}\[([^\]\r\n]+)\]:", re.M)
#: The label of a full reference link, `[text][label]`.
REFERENCE_LABEL = re.compile(r"\]\[[^\]\r\n]*\]")
#: The document's own leading YAML front matter, if it has one, closed by a
#: '---' line or by a '---' that ends the file. \r? tolerates CRLF, which
#: --fix preserves rather than silently normalising to LF.
FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---(?:\r?\n|\Z)", re.S)
#: word((\?_word)+)(.extension)* -- letters and digits in any script, so
#: `naïve_value_count` is one candidate, and never started or ended inside a
#: longer word. The backslash before a join underscore is OPTIONAL so the
#: whole contiguous run (escaped or not) is matched as one candidate;
#: `_is_valid_identifier` then rejects the candidate whole when any join was
#: escaped, rather than resuming the match past the escape.
CANDIDATE = re.compile(r"(?<![\w\\])[^\W_]+(?:\\?_[^\W_]+)+(?:\.\w+)*(?!\w)")

RULE = "MD-US"


@dataclass(frozen=True)
class Finding:
    line: int
    identifier: str


def _is_closing_fence(line: str, char: str, length: int) -> bool:
    """True when `line` closes a fence that opened with `length` copies of
    `char`: the same character, at least that many, then nothing but
    whitespace. A line such as "```not-a-close" fails this (CommonMark's own
    rule: a closing fence carries no info string), so it stays content."""
    stripped = line.rstrip("\r\n")
    marker = FENCE_LINE.match(stripped)
    if not marker or marker.group(1)[0] != char or len(marker.group(1)) < length:
        return False
    return stripped[marker.end():].strip() == ""


def _fence_ranges(text: str) -> list[tuple[int, int]]:
    """Byte ranges of top-level fenced code blocks. A fence opened but never
    closed protects to the end of the file, rather than protecting nothing."""
    ranges: list[tuple[int, int]] = []
    offset = 0
    opener: tuple[str, int, int] | None = None
    for line in text.splitlines(keepends=True):
        if opener is None:
            marker = FENCE_LINE.match(line)
            if marker:
                opener = (marker.group(1)[0], len(marker.group(1)), offset)
        elif _is_closing_fence(line, opener[0], opener[1]):
            ranges.append((opener[2], offset + len(line)))
            opener = None
        offset += len(line)
    if opener is not None:
        ranges.append((opener[2], len(text)))
    return ranges


def _link_target_end(text: str, open_paren: int) -> int | None:
    """The offset right after the `)` balancing the `(` at `open_paren`, or
    None when it never balances before a line ending. A backslash escapes
    the next character, so an escaped paren never changes the depth."""
    depth = 0
    pos = open_paren
    while pos < len(text):
        char = text[pos]
        if char == "\\":
            pos += 2
            continue
        if char in "\r\n":
            return None
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    return None


def _link_target_ranges(text: str) -> list[tuple[int, int]]:
    """The `(...)` right after every link or image's `]`, parentheses
    balanced: `[x](docs/(v1)/foo_bar.md)` protects the whole destination,
    not just up to the first unescaped `)`."""
    ranges = []
    for match in LINK_TARGET_OPEN.finditer(text):
        end = _link_target_end(text, match.end() - 1)
        if end is not None:
            ranges.append((match.start(), end))
    return ranges


def _reference_label_ranges(text: str) -> list[tuple[int, int]]:
    """Reference link labels: the `[label]` of `[text][label]`, and every
    `[label]` naming a label the document defines (a collapsed or shortcut
    reference), matched case-insensitively as CommonMark matches labels. All
    defined labels go into one pattern, so the text is scanned once."""
    ranges = [match.span() for match in REFERENCE_LABEL.finditer(text)]
    labels = sorted({match.group(1) for match in REFERENCE_DEF_LABEL.finditer(text)}, key=len, reverse=True)
    if labels:
        uses = re.compile(r"\[(?:" + "|".join(map(re.escape, labels)) + r")\]", re.I)
        ranges.extend(match.span() for match in uses.finditer(text))
    return ranges


def _protected_ranges(text: str) -> list[tuple[int, int]]:
    """Every character range an identifier must never be found in."""
    ranges = list(_fence_ranges(text))
    front_matter = FRONTMATTER.match(text)
    if front_matter:
        ranges.append(front_matter.span())
    ranges.extend(_link_target_ranges(text))
    ranges.extend(_reference_label_ranges(text))
    for pattern in (INLINE_CODE, ANGLE, BARE_URL, REFERENCE_DEF):  # O(1): four fixed patterns, one linear scan each
        ranges.extend(match.span() for match in pattern.finditer(text))
    return ranges


class _Protected:
    """The protected ranges merged into sorted, disjoint intervals, so each
    overlap test is one binary search rather than a scan of every range."""

    def __init__(self, ranges: list[tuple[int, int]]) -> None:
        self.starts: list[int] = []
        self.ends: list[int] = []
        for start, end in sorted(ranges):
            if self.ends and start <= self.ends[-1]:
                self.ends[-1] = max(self.ends[-1], end)
            else:
                self.starts.append(start)
                self.ends.append(end)

    def overlaps(self, start: int, end: int) -> bool:
        """True when [start, end) overlaps a protected interval. The last
        interval starting before `end` has the largest end among them all,
        because the intervals are disjoint and sorted."""
        index = bisect.bisect_left(self.starts, end) - 1
        return index >= 0 and self.ends[index] > start


def _is_valid_identifier(candidate: str) -> bool:
    """False when any underscore join in `candidate` was escaped -- the
    whole contiguous run is rejected, never salvaged from the escape on."""
    return "\\_" not in candidate


def _unprotected_matches(text: str) -> list[re.Match]:
    protected = _Protected(_protected_ranges(text))
    return [match for match in CANDIDATE.finditer(text)
            if _is_valid_identifier(match.group(0)) and not protected.overlaps(match.start(), match.end())]


def find_identifiers(text: str) -> list[Finding]:
    """Every MD-US finding in `text`, in the order it appears. A finding
    names the 1-based line its identifier starts on, and the identifier
    text itself, exactly as written (extension included for a file name)."""
    return [Finding(text.count("\n", 0, match.start()) + 1, match.group(0))
            for match in _unprotected_matches(text)]


def fix_text(text: str) -> str:
    """`text` with every MD-US finding wrapped whole in backticks. Returns
    `text` unchanged when there is nothing to fix. Idempotent: calling this
    again on the result finds nothing more to wrap, because each wrapped
    identifier is now a single-backtick inline code span."""
    matches = _unprotected_matches(text)
    if not matches:
        return text
    pieces: list[str] = []
    last = 0
    for match in matches:
        pieces.append(text[last:match.start()])
        pieces.append(f"`{match.group(0)}`")
        last = match.end()
    pieces.append(text[last:])
    return "".join(pieces)


def _read(path: str) -> str | None:
    """`path`'s text, with its line endings kept exactly as written (no
    universal-newline translation) -- `--fix` must change only the matched
    spans, never silently turn every CRLF in the file into a bare LF."""
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except OSError as exc:
        print(f"markdown_underscore_check: cannot read {exc.filename}: {exc.strerror}", file=sys.stderr)
        return None


def _write(path: str, text: str) -> None:
    """Write `text` back with no newline translation, the write half of the
    pair that keeps `_read`'s preserved line endings on disk."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _report(path: str, findings: list[Finding]) -> None:
    """Print one line per finding: `path:line: MD-US identifier`."""
    for finding in findings:
        print(f"{path}:{finding.line}: {RULE} {finding.identifier}")


def main(argv: list[str] | None = None) -> int:
    """Report every MD-US finding in each FILE. With --fix, rewrite each
    file instead, wrapping every finding in backticks, and report the count.

    usage: grip-py lib/markdown_underscore_check.py [--fix] FILE [FILE ...]
    """
    args = sys.argv[1:] if argv is None else argv
    if {"-h", "--help"} & set(args):
        print(main.__doc__.strip().splitlines()[-1])
        return 0
    fix = "--fix" in args
    paths = [arg for arg in args if arg != "--fix"]
    if not paths:
        print("markdown_underscore_check: no FILE given", file=sys.stderr)
        return 2
    seen_any = False
    for path in paths:
        text = _read(path)
        if text is None:
            return 2
        findings = find_identifiers(text)
        seen_any = seen_any or bool(findings)
        if not fix:
            _report(path, findings)
            continue
        if findings:
            _write(path, fix_text(text))
            print(f"{path}: wrapped {len(findings)} identifier(s)")
    return 0 if fix else (1 if seen_any else 0)


if __name__ == "__main__":
    sys.exit(main())
