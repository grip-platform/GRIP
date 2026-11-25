#!/usr/bin/env python3
"""LinkedIn post craft — deterministic Unicode toolkit + anti-fluff floor.

LinkedIn renders no markdown but DOES render Unicode Mathematical Alphanumeric
glyphs as bold/italic inside plain text. This module is the *mechanical floor*
(L1) of the linkedin-post-craft skill: pure, dependency-free, and Goodhart-test
anchored. The AI "reason + review" layer (L2) lives in the SKILL.md prose and is
applied by the model — it never relaxes this floor.

Two capabilities:

1. Glyph conversion — `bolden`, `italicise`, `frame`, and the `GLYPHS` table
   that maps each named structural element to its exact codepoint(s). LinkedIn
   shows the result as styled text; the underlying characters stay copy-pastable.

2. Anti-fluff detection — `antifluff_violations` is the deterministic half of the
   cross-ref to the `authentic-writing` skill. It returns the banned marketing
   tokens present in a draft so the gate "fires by construction, not opt-in"
   (issue #3176 acceptance criterion). It NEVER mutates text — it reports, the
   author (or the model) fixes the root cause.

Soundness note: only ASCII letters/digits are remapped. Any character outside
[A-Za-z0-9] (spaces, punctuation, emoji, already-styled glyphs) passes through
unchanged, so the transform is total and idempotent on already-styled input.
"""
from __future__ import annotations

import re
import unicodedata

# --- Mathematical Sans-Serif blocks (the glyphs LinkedIn renders styled) ------
# Bold:   A→U+1D5D4  a→U+1D5EE  0→U+1D7EC
# Italic: A→U+1D608  a→U+1D622  (the italic block has NO digit glyphs)
_BOLD_UPPER, _BOLD_LOWER, _BOLD_DIGIT = 0x1D5D4, 0x1D5EE, 0x1D7EC
_ITAL_UPPER, _ITAL_LOWER = 0x1D608, 0x1D622

# Named structural glyphs — the 7-element toolkit from the issue table.
GLYPHS = {
    "corner_open": "⌜",     # ⌜  A/B/C structural framing
    "corner_close": "⌟",    # ⌟
    "rule": "━" * 3,        # ━━━ section breathing room
    "bullet": "▸",          # ▸  tracked-step bullet
    "arrow": "→",           # → step transition
    "middot": "·",          # ·  URL handle joins
    "diamond": "◆",         # ◆  terminal punctuation marker
}

# Deterministic anti-fluff floor — mirrors authentic-writing's banned tables.
# Lower-cased word/phrase tokens; matched case-insensitively on word boundaries.
BANNED_WORDS = (
    "game-changer", "game changer", "revolutionary", "seamlessly", "seamless",
    "elegant", "powerful", "innovative", "cutting-edge", "leverage", "utilize",
    "utilise", "synergy",
)
BANNED_PHRASES = (
    "it's important to note that", "as mentioned above", "furthermore",
    "moreover", "in order to", "at the end of the day", "moving forward",
    "let's dive in", "without further ado", "that being said",
    "needless to say",
)


def _shift(text: str, upper: int, lower: int, digit: int | None) -> str:
    """Remap ASCII letters/digits to a Math-Alphanumeric block; pass the rest.

    digit=None (italic block has no digits) leaves digits as ASCII.
    """
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(upper + (ord(ch) - 65)))
        elif "a" <= ch <= "z":
            out.append(chr(lower + (ord(ch) - 97)))
        elif "0" <= ch <= "9" and digit is not None:
            out.append(chr(digit + (ord(ch) - 48)))
        else:
            out.append(ch)
    return "".join(out)


def bolden(text: str) -> str:
    """ASCII → Math Sans-Serif Bold (𝗕𝗼𝗹𝗱). Emphasis; replaces `**bold**`."""
    return _shift(text, _BOLD_UPPER, _BOLD_LOWER, _BOLD_DIGIT)


def italicise(text: str) -> str:
    """ASCII letters → Math Sans-Serif Italic (𝘐𝘵𝘢𝘭𝘪𝘤). Meta-asides.

    The Unicode italic block defines no digit glyphs, so digits stay ASCII
    (rendering an italic digit would silently corrupt to a missing glyph).
    """
    return _shift(text, _ITAL_UPPER, _ITAL_LOWER, None)


def frame(label: str) -> str:
    """Wrap a label in corner brackets for A/B/C structural framing: ⌜ label ⌟."""
    return f"{GLYPHS['corner_open']} {label} {GLYPHS['corner_close']}"


def step(text: str) -> str:
    """Render a tracked-step bullet: ▸  text."""
    return f"{GLYPHS['bullet']}  {text}"


def antifluff_violations(text: str) -> list[str]:
    """Return banned marketing tokens present in `text` (deduped, ordered).

    The deterministic floor of the authentic-writing anti-fluff gate. Empty list
    == clean. Reports only; never rewrites (fix the root cause, per no-guardrail-
    evasion). Word tokens match on boundaries so "use" never trips "utilise".
    """
    low = text.lower()
    found: list[str] = []
    for word in BANNED_WORDS:
        if re.search(r"\b" + re.escape(word) + r"\b", low) and word not in found:
            found.append(word)
    for phrase in BANNED_PHRASES:
        if phrase in low and phrase not in found:
            found.append(phrase)
    return found


def is_styled(text: str) -> bool:
    """True if `text` contains any Math-Alphanumeric styled glyph (already bold/italic)."""
    return any(
        unicodedata.name(ch, "").startswith("MATHEMATICAL")
        for ch in text
    )


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    sample = "GRIP ships provability"
    print("bold  :", bolden(sample))
    print("italic:", italicise("quick shill"))
    print("frame :", frame("A"))
    print("step  :", step("install in one command"))
    print("fluff :", antifluff_violations("This powerful, game-changer tool"))
