"""Insight text hygiene for the learning pipeline (GRIP#8068).

Two defects reached ``rules/learned-insights.md``, an always-loaded rules file, on
2026-10-11. Gate-block events carried the first 200 characters of a tool's output as
"insight" text, so the insight was a tool-input dict, a path or a stdout fragment and
never a lesson. And the renderer cut every rule title at 60 characters mid-string, so
three headers ended inside an open quote. This module holds the one rule both sides
use: an insight is prose about a lesson, never a payload, and a title ends at a word.

Pure functions, no I/O. Both the capture side (hooks/rsi-posttool-hook.py) and the
render side (lib/learning_detector.py) call them, so rows written before the fix are
cleaned at render time and rows written after it never carry a payload at all.
"""
from __future__ import annotations

import re

#: A serialised mapping a tool echoed back: a brace group that holds a quoted key
#: before a colon. A template or shell expression (``{LETTER}``, ``${TMPDIR}``, an
#: f-string's ``{value}``) holds no quoted key and is kept, because a lesson about a
#: command needs its exact token (Codex on GRIP#8080). Applied until nothing
#: changes, so a nested dump collapses from the inside out whatever its depth.
_MAPPING_RE = re.compile(r"\{[^{}]*['\"][^{}]*:[^{}]*\}")
_MAX_NESTING = 64
#: An absolute path of two or more segments, whatever its root, not preceded by a
#: word character or another slash: ``/workspace/GRIP/a.md`` and ``/tmp/x/y`` match
#: whole; ``and/or``, a date ``2026/10/11`` and the ``//host/a`` of a URL do not.
_PATH_RE = re.compile(r"(?<![\w/])/(?:[\w.~-]+/)+[\w.~-]*")
_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\w")

PAYLOAD = "<payload>"
PATH = "<path>"
MARKER = "…"   # one ellipsis character, so a cut title says it was cut


def cut_at_word(text: str, limit: int, marker: str = MARKER) -> str:
    """``text`` cut to at most ``limit`` characters at a word boundary, with the
    marker appended when anything was cut. Trailing openers and separators are
    dropped first, so a cut never ends inside a bracket or after a colon. When the
    first token alone is longer than the limit (a URL, a hash, a backticked path),
    that whole token is kept rather than cut inside, so the result may exceed the
    limit but never ends mid-token."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    if " " in head:
        head = head[: head.rfind(" ")]
    else:
        end = text.find(" ", limit)
        if end < 0:
            return text                      # a single long token: nothing to cut at
        head = text[:end]
    return head.rstrip(" ,;:(['\"{") + marker


def _without_mappings(text: str) -> str:
    for _ in range(_MAX_NESTING):
        replaced = _MAPPING_RE.sub(PAYLOAD, text)
        if replaced == text:
            return text
        text = replaced
    return text


def clean_insight_text(text: str, limit: int | None = 200) -> str:
    """``text`` with every serialised mapping and absolute path replaced by a
    placeholder and whitespace collapsed; cut at a word boundary to ``limit`` when
    one is given, kept whole when ``limit`` is None (a rule body is never cut, only
    its title is)."""
    cleaned = _without_mappings(text.replace("\n", " "))
    cleaned = _PATH_RE.sub(PATH, cleaned)
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    return cleaned if limit is None else cut_at_word(cleaned, limit)


def says_something(cleaned: str) -> bool:
    """False when a cleaned text has no word left once the placeholders are gone:
    punctuation, whitespace and the cut marker alone are a payload with its payload
    removed, not a lesson."""
    residue = cleaned.replace(PAYLOAD, "").replace(PATH, "")
    return _WORD_RE.search(residue) is not None


def title_of(text: str, limit: int = 60) -> str:
    """A rule title from an insight text: unchanged below the limit (a trailing full
    stop dropped), otherwise cut at a word with the marker. A title that ends
    mid-string ("...'filePath': '/private") was the defect this replaces."""
    flat = _WS_RE.sub(" ", text).strip()
    if len(flat) <= limit:
        return flat.rstrip(". ")
    return cut_at_word(flat, limit)


def gate_block_summary(tool_name: str, shielded_output: str, limit: int = 160) -> str:
    """The learn-event text for a gate block, or "" when the output holds no lesson.

    ``shielded_output`` is the tool output AFTER the redaction shield, never before:
    a path or a dict dump is removed here for readability, not for secrecy. The result
    names the tool and keeps the gate's own words, payload-free and cut at a word;
    a block whose output was only a payload yields "" so no learn-event is sent."""
    body = clean_insight_text(shielded_output, limit)
    if not says_something(body):
        return ""
    return f"Gate blocked {tool_name}: {body}"
