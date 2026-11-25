"""TUI++ signature reveal renderer for /activate-grip-tui (issue #2520).

Renders a Swiss-Nihilism signature block for the operator's chosen letter.
Decoration is built from monospace ASCII glyphs only — no terminal escape
sequences — so chunk boundaries can never split mid-byte regardless of how
the rendering layer streams output (see lib/learned-insights.md
skill-20260331004751 for the underlying ANSI-bug pattern).
"""

from __future__ import annotations

_TOP_RULE = "═" * 47
_PADDING_LINE = " " * 47


def render_signature(letter: str) -> str:
    """Render the large signature block for an operator's chosen letter.

    Args:
        letter: A single uppercase ASCII letter A-Z.

    Returns:
        Multi-line string containing the rendered block. Trailing newline
        included so callers can write directly to stdout without ambiguity.

    Raises:
        ValueError: if `letter` is not a single uppercase A-Z character.
    """
    if not isinstance(letter, str) or len(letter) != 1 or not ("A" <= letter <= "Z"):
        raise ValueError(
            f"signature must be a single uppercase letter A-Z, got {letter!r}"
        )

    sig = f"{letter}>>"
    return (
        f"\n{_TOP_RULE}\n"
        f"{_PADDING_LINE}\n"
        f"    {sig}\n"
        f"{_PADDING_LINE}\n"
        f"{_TOP_RULE}\n"
        f"\n"
        f"This is your signature. Type it before any prompt to mark direct\n"
        f"instruction mode. GRIP will recognise {sig} as you, the operator.\n"
    )


def render_flavour(letter: str) -> str:
    """One-line flavour text post-reveal — keeps the moment from feeling clinical."""
    if not isinstance(letter, str) or len(letter) != 1 or not ("A" <= letter <= "Z"):
        raise ValueError(
            f"signature must be a single uppercase letter A-Z, got {letter!r}"
        )
    return f"Welcome, {letter}>>. The system has registered your mark."
