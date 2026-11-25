"""Subprocess environment sanitizer.

Absorbed from multica (W4a research-council audit, grade-A pattern).
See: /tmp/multica-analysis.md section on the blocklist pattern.

Adaptation: strip a fixed list of prefixes and exact names from any mapping
that will be handed to a spawned subprocess worker, so that session-local
state cannot leak into isolated workers.
"""

import os
from typing import Iterable, Mapping, Optional

BLOCKED_PREFIXES: tuple[str, ...] = (
    "GRIP_",
    "CLAUDE_",
)

BLOCKED_EXACT = frozenset({
    "HOME",
    "PATH",
    "SHELL",
})


def is_blocked(key: str) -> bool:
    """Return True if the key should be stripped from a spawned worker."""
    if not key:
        return False
    if key in BLOCKED_EXACT:
        return True
    return any(key.startswith(p) for p in BLOCKED_PREFIXES)


def sanitise_env(
    env: Optional[Mapping[str, str]],
    *,
    allow: Iterable[str] = (),
) -> dict:
    """Return a new dict with blocked keys removed.

    Args:
        env: Source mapping. None or empty is handled safely (returns {}).
        allow: Explicit allowlist that bypasses the blocklist.

    Returns:
        A fresh dict. The input mapping is never mutated.
    """
    if not env:
        return {}
    allowed = frozenset(allow)
    return {k: v for k, v in env.items() if k in allowed or not is_blocked(k)}


def spawn_env(base: Optional[Mapping[str, str]] = None, **extra: str) -> dict:
    """Build a sanitised mapping for a spawned worker.

    Defaults to the current process mapping, strips blocked keys, then
    applies caller-supplied additions on top (which are NOT re-checked).
    """
    source = os.environ if base is None else base
    clean = sanitise_env(source)
    clean.update(extra)
    return clean
