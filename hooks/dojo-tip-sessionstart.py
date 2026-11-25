#!/usr/bin/env python3
"""Session-start tip card — ONE terse tip line injected into context.

The tip card at session start (operator decision #4). Draws from the same
single renderer as the dojo shell: a fresh learned tip when one exists,
otherwise the curated tip of the day. Delight kill-line: exactly one functional
line, no animation, no celebration — plain text (no ANSI) so it survives
context injection.

Wired as a SessionStart hook in settings.json. Read-only: it never advances the
learned-scan cursor (the daily #daily-chat poster owns that).
"""

from __future__ import annotations

import os
import sys

CLAUDE_DIR = os.environ.get("CLAUDE_DIR") or os.path.expanduser("~/.claude")
if CLAUDE_DIR not in sys.path:
    sys.path.insert(0, CLAUDE_DIR)


def main() -> int:
    from lib.dojo_tip import load_curated_tips, pick_tip, render_card

    tip, source = pick_tip(advance=False)
    if not tip:
        return 0
    if source == "curated":
        total = load_curated_tips().get("total_tips") or 0
    else:
        total = None
    line = render_card(tip, source=source, total=total, colour=False)
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
