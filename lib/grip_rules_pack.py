"""The rules pack: the shape in which GRIP's rules ship to a stranger.

Plain English: each rule file carries a mandate, and most also open with an
"Origin:" paragraph, the dated story of the incident that produced the rule, in
the operator's voice and about his sessions. The mandate is for everyone; the
story is his. The pack strips the dated origin paragraphs and keeps every other
byte, so the shipped rule still binds exactly as the live one does.

The contract (the one-folder product plan, item B4, 2026-10-18; V>>'s decision 3,
that the handle itself may stay in attributions):

* A paragraph is stripped when it STARTS with the origin marker (``Origin:``,
  ``Origin,`` or the bold forms) AND carries a date of the form ``2026-05-15`` or
  ``2026-05`` anywhere in it. Both are required: an undated ``**Origin**:`` that
  explains a design decision stays, and so does ``Origin memory:``, which is a
  pointer, not a story.
* A paragraph is the maximal run of non-blank lines; the blank line after a
  stripped paragraph goes with it, so no double blank is left behind.
* Nothing inside a fenced code block is touched.
* Nothing else changes: not a heading, not a mandate, not an attribution. The
  guarantee is byte for byte: every kept line is an original line, in its
  original order, with its original line ending. A file's final newline stays;
  the one case that adds a byte is a file that ended WITHOUT a newline whose
  last paragraph was stripped, which then ends with the newline of the blank
  line that preceded that paragraph (pinned by a test). Nothing here
  normalises whitespace, and nothing should be added that does.

The hypothesis this rests on, with its falsifier, is in the plan: stripping only
dated origin paragraphs loses no mandate; a rule unreadable without its origin
refutes it, by 2026-11-01.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ORIGIN_START = re.compile(r"^\*{0,2}Origin\*{0,2}\s*[:,]\*{0,2}(?:\s|$)")
DATED = re.compile(r"\b20\d\d-\d\d(?:-\d\d)?\b")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _paragraphs(lines: list[str]):
    """(start, end) index pairs of maximal non-blank runs, skipping fenced code."""
    in_fence = False
    start = None
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            if start is not None:
                yield start, i
                start = None
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.strip():
            if start is None:
                start = i
        elif start is not None:
            yield start, i
            start = None
    if start is not None:
        yield start, len(lines)


def dated_origin_paragraphs(text: str) -> list[tuple[int, int]]:
    """Line ranges [start, end) of the paragraphs the pack strips."""
    lines = text.split("\n")
    return [(s, e) for s, e in _paragraphs(lines)
            if ORIGIN_START.match(lines[s]) and any(DATED.search(line) for line in lines[s:e])]


def strip_dated_origins(text: str) -> tuple[str, int]:
    """The text without its dated origin paragraphs, and how many were removed."""
    lines = text.split("\n")
    drop: set[int] = set()
    ranges = dated_origin_paragraphs(text)
    for s, e in ranges:
        drop.update(range(s, e))
        if e < len(lines) and not lines[e].strip():
            drop.add(e)  # the blank line after the paragraph goes with it
    kept = [line for i, line in enumerate(lines) if i not in drop]
    return "\n".join(kept), len(ranges)


def pack_rule(src: Path, dst: Path) -> int:
    """Write the packed form of one rule file; returns the paragraphs stripped."""
    text = Path(src).read_text(encoding="utf-8")
    packed, count = strip_dated_origins(text)
    Path(dst).write_text(packed, encoding="utf-8")
    shutil.copymode(src, dst)
    return count
