"""Insight harvester — extracts ★ Insight blocks from assistant output.

Part of the Auto-Insight Memory pipeline. Reads raw assistant text and
returns structured ``Insight`` records, one per ``★ Insight ─── … ───``
block in the explanatory output style.

The harvester is the first stage: it does NOT score or persist. Downstream
stages (``insight_value_scorer``, ``auto_memory_writer``) decide whether a
harvested block is worth saving.

Design constraints:
- Pure function: no I/O, no state.
- Cheap fast-path: early exit if no ``★ Insight`` substring present.
- Compiled regexes at module load (no per-call compile overhead).
- Handles multiple blocks per text, unicode content, varying dash counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Insight:
    """A single harvested insight block with position metadata."""

    content: str
    start_offset: int
    end_offset: int

    @property
    def line_count(self) -> int:
        return len(self.content.splitlines())


# Compile once at module load. The opening marker is the literal ★ Insight
# followed by at least 5 dashes. Closing is a line of at least 5 dashes
# (no ★). We use MULTILINE so ^/$ match line boundaries.
# Backtick variants: the Explanatory output style wraps markers in backticks
# for inline-code formatting, so both `` `★ Insight ─────` `` and plain
# ``★ Insight ─────`` must match.
_OPEN_RE = re.compile(r"^`?★\s*Insight\s*─{5,}`?\s*$", re.MULTILINE)
_CLOSE_RE = re.compile(r"^`?─{5,}`?\s*$", re.MULTILINE)

# Cheap fast-path sentinel — if this substring is absent, skip regex work.
_FAST_PATH_SENTINEL = "★ Insight"


def harvest(text: str) -> list[Insight]:
    """Return all Insight blocks in ``text``. Empty list if none."""
    if not text or _FAST_PATH_SENTINEL not in text:
        return []

    insights: list[Insight] = []
    pos = 0
    while pos < len(text):
        open_match = _OPEN_RE.search(text, pos)
        if not open_match:
            break
        content_start = open_match.end()
        close_match = _CLOSE_RE.search(text, content_start)
        if not close_match:
            # Unterminated block — skip past the opener and keep scanning.
            pos = content_start
            continue
        content = text[content_start : close_match.start()].strip("\n")
        if content.strip():
            insights.append(
                Insight(
                    content=content,
                    start_offset=open_match.start(),
                    end_offset=close_match.end(),
                )
            )
        pos = close_match.end()
    return insights


def harvest_last(text: str) -> Insight | None:
    """Return the final Insight in ``text``, or None if none present.

    Useful when only the latest assistant message is being scanned (the
    typical Stop-hook case).
    """
    found = harvest(text)
    return found[-1] if found else None
