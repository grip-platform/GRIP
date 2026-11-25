"""
tui_links.py — OSC 8 terminal hyperlink library for GRIP.

Provides clickable file:// and https:// links in terminals that support OSC 8
(iTerm2, WezTerm, Kitty, Ghostty). Degrades gracefully to plain text elsewhere.

Usage:
    from tui_links import file_link, url_link, supports_hyperlinks

    print(file_link("/path/to/file.md", "Open file"))
    print(url_link("https://example.com", "Visit"))
"""

from __future__ import annotations

import os
from pathlib import Path

# Terminals confirmed to support OSC 8 hyperlinks
_HYPERLINK_TERMINALS = frozenset({
    "iTerm.app",
    "WezTerm",
    "xterm-kitty",
    "ghostty",
})

# Additional detection via TERM_PROGRAM_VERSION or TERM
_HYPERLINK_TERMS = frozenset({
    "xterm-kitty",
})


def supports_hyperlinks() -> bool:
    """Check if the current terminal supports OSC 8 hyperlinks.

    Detection uses TERM_PROGRAM (set by most modern terminals) and
    falls back to TERM for kitty which sets xterm-kitty.
    """
    term_program = os.environ.get("TERM_PROGRAM", "")
    if term_program in _HYPERLINK_TERMINALS:
        return True
    term = os.environ.get("TERM", "")
    if term in _HYPERLINK_TERMS:
        return True
    # VTE-based terminals (GNOME Terminal, Tilix) support OSC 8
    if os.environ.get("VTE_VERSION"):
        return True
    return False


def file_link(path: str, display: str | None = None) -> str:
    """Create an OSC 8 file:// hyperlink.

    Args:
        path: Absolute or relative file path. Resolved to absolute.
        display: Visible text. Defaults to the filename.

    Returns:
        OSC 8 escaped string if supported, plain display text otherwise.
    """
    resolved = str(Path(path).resolve())
    label = display or Path(path).name
    if supports_hyperlinks():
        return f"\033]8;;file://{resolved}\033\\{label}\033]8;;\033\\"
    return label


def url_link(url: str, display: str | None = None) -> str:
    """Create an OSC 8 https:// hyperlink.

    Args:
        url: Full URL (https://...).
        display: Visible text. Defaults to the URL.

    Returns:
        OSC 8 escaped string if supported, plain display text otherwise.
    """
    label = display or url
    if supports_hyperlinks():
        return f"\033]8;;{url}\033\\{label}\033]8;;\033\\"
    return label
