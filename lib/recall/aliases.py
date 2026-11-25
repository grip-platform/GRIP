"""Subject normalisation + alias expansion for the recall substrate.

Deterministic, pure stdlib. The alias table is the exact/alias lookup basis
(F5: semantic similarity never authorises a negative claim — these keys do).
"""

from __future__ import annotations

import re

_NORM_RE = re.compile(r"[^a-z0-9]+")

# Seed aliases: key -> list of equivalent phrases. Keys and values are
# normalised at expansion time, so entries may be written naturally.
DEFAULT_ALIASES: dict[str, list[str]] = {
    "aod": ["acknowledgement of debt", "aod-arnold", "aod-star"],
    "arnold": ["k>>"],
}


def normalise(subject: str) -> str:
    """Lowercase, punctuation to spaces, whitespace collapsed."""
    return _NORM_RE.sub(" ", str(subject or "").lower()).strip()


def _groups(extra: dict | None) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {k: list(v) for k, v in DEFAULT_ALIASES.items()}
    for key, vals in (extra or {}).items():
        merged.setdefault(key, [])
        merged[key].extend(list(vals))
    return merged


def expand(subject: str, extra: dict | None = None) -> set[str]:
    """All normalised lookup keys equivalent to ``subject``.

    A group joins the result when the normalised subject — or any single
    token of it — exactly matches the group's normalised key or one of its
    normalised aliases. Exact-token membership only; never fuzzy.
    """
    norm = normalise(subject)
    keys: set[str] = {norm} if norm else set()
    probes = {norm} | set(norm.split())
    for group_key, vals in _groups(extra).items():
        group = {normalise(group_key)} | {normalise(v) for v in vals}
        group.discard("")
        if probes & group:
            keys |= group
    keys.discard("")
    return keys
