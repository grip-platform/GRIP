"""Dojo theme — Swiss-nihilism design tokens for the playable dojo.

Extracts the glyphs and colours that were previously literals inside
``lib.hal.settings_tui`` (``╌``, ``▸``, the dim/bold frame) into one pure
module the dojo tip card and the curses shell share. One accent (orange-600),
paper-grey, sharp corners, monospace meta — per
``skills/swiss-nihilism-design`` 7 laws.

Pure and testable by construction: no curses import, no I/O, no network.
Every token is a plain string; ``wrap`` / ``strip`` are the only logic, and
``strip`` is what lets a test (or a plain-text sink) assert on the bare text
without caring about escape codes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ── Colour tokens (hex per swiss-nihilism-design §4) ──────────────────────
# orange-600 is the single accent (#ea580c). a11y-orange is the accessible
# variant for small text on paper-grey. Paper-grey itself is documented but
# not used as a background in the terminal card (settings_tui sets no fill).
HEX = {
    "accent": "#ea580c",      # orange-600 — the one accent
    "a11y_orange": "#9a3412",  # accessible orange on paper
    "paper": "#EAEAEA",       # paper-grey (documented, not painted)
    "black": "#000000",
    "gray": "#525252",        # a11y-gray (secondary text)
}

# ANSI codes. The accent uses truecolor so it is the exact orange-600, with a
# 256-colour fallback (208 ≈ #ff8700) for terminals that ignore truecolor.
_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "accent": "\033[38;5;208m",
    "accent_true": "\033[38;2;234;88;12m",
    "a11y_orange": "\033[38;5;130m",
    "gray": "\033[38;5;240m",
    "black": "\033[38;5;0m",
}

# ── Glyphs (shared by tip card + curses shell) ─────────────────────────────
GLYPHS = {
    "hr": "╌",          # the settings_tui header/footer bar glyph
    "cursor": "▸",      # selected-row cursor
    "bar": "─",         # plain separator
    "rail": "│",        # HAL left-rail (cli.py)
    "try": "›",         # "try it" lead
}

# ── Badges (kept tiny; the dojo is not a model browser) ────────────────────
# Lesson badges for the shell body; no provider badges are needed here.
BADGES = {
    "lesson": {"L1": "prompt-coach", "L2": "save + recall", "L3": "converge"},
}


@dataclass(frozen=True)
class DojoTheme:
    """A frozen, pickle-safe bundle of the tokens above.

    Frozen so a renderer cannot mutate a shared theme mid-run; the dojo is
    stateless and the theme is a constant.
    """

    name: str = "swiss-nihilism"
    accent: str = HEX["accent"]
    paper: str = HEX["paper"]
    glyph_hr: str = GLYPHS["hr"]
    glyph_cursor: str = GLYPHS["cursor"]
    glyph_try: str = GLYPHS["try"]


# The single shared theme instance. Consumers import this, they never build
# their own — one accent, one frame, everywhere.
THEME = DojoTheme()


# ── ANSI helpers ───────────────────────────────────────────────────────────
def colour(name: str) -> str:
    """Return the ANSI escape for a named colour (``accent``, ``gray``, …)."""
    return _ANSI.get(name, "")


def wrap(text: str, name: str, *, bold: bool = False, dim: bool = False) -> str:
    """Wrap ``text`` in the named colour (plus optional bold/dim), resetting.

    Returns the text unchanged (no codes) when the named colour is unknown,
    so a typo in a token name degrades to plain text rather than mangling it.
    """
    code = _ANSI.get(name, "")
    if not code and not bold and not dim:
        return text
    prefix = (colour("bold") if bold else "") + (colour("dim") if dim else "") + code
    return f"{prefix}{text}{_ANSI['reset']}" if prefix else text


def strip(text: str) -> str:
    """Remove every ANSI escape sequence from ``text``.

    The one function tests lean on to assert on content without caring about
    colour. Replaces the escape with '' rather than trying to emulate width.
    """
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ── Frame helpers (shared string shapes) ───────────────────────────────────
def header_line(title: str, width: int, glyph: Optional[str] = None) -> str:
    """Render a ``╌╌╌ title ╌╌╌`` centred band of ``width`` columns.

    Mirrors ``settings_tui._draw_header``'s shape without curses. Used by both
    the ANSI tip card (title) and, with ``glyph='─'``, a plain rule.
    """
    g = glyph or GLYPHS["hr"]
    pad = max(1, (width - len(title)) // 2)
    bar = g * pad + title + g * pad
    return bar[: max(0, width - 1)]


def footer_line(keys: str, width: int, glyph: Optional[str] = None) -> str:
    """Render a ``╌╌╌ keymap ╌╌╌`` band (settings_tui._draw_footer shape)."""
    g = glyph or GLYPHS["hr"]
    return (g * 3 + " " + keys + " " + g * 3)[: max(0, width - 1)]
