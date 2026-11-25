"""Small helpers shared across the KERN modules."""
from __future__ import annotations

from typing import Iterable


def duplicates(items: Iterable[str]) -> list[str]:
    """Every value that occurs more than once, sorted."""
    seen: set[str] = set()
    dupes: set[str] = set()
    for item in items:
        (dupes if item in seen else seen).add(item)
    return sorted(dupes)
