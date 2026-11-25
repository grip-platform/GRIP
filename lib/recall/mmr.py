"""Merkle Mountain Range — the index's audit commitment (F1/F4).

Pure stdlib, deterministic. Byte convention: every leaf is a lowercase
sha256 hex string (``schema.content_hash`` output); an interior/bagged node
is ``sha256((left_hex + right_hex).encode("utf-8")).hexdigest()`` — i.e. we
hash the UTF-8 concatenation of the two hex strings, never raw digest bytes.

State is JSON-serialisable: ``{"peaks": [{"height": int, "hash": str}, ...],
"count": N}`` with peaks ordered oldest/highest first (append order).
Proofs are generated from the full ordered leaf list (peaks alone discard
the interior nodes an inclusion proof needs).
"""

from __future__ import annotations

import hashlib

EMPTY_ROOT = hashlib.sha256(b"grip-recall-mmr:empty").hexdigest()


def _h(left: str, right: str) -> str:
    return hashlib.sha256((left + right).encode("utf-8")).hexdigest()


def empty_state() -> dict:
    return {"peaks": [], "count": 0}


def append_leaf(leaf_hash: str, state: dict) -> dict:
    """Return a NEW state with ``leaf_hash`` appended. O(log n)."""
    peaks = [dict(p) for p in state.get("peaks", [])]
    peaks.append({"height": 0, "hash": str(leaf_hash)})
    while len(peaks) >= 2 and peaks[-1]["height"] == peaks[-2]["height"]:
        right = peaks.pop()
        left = peaks.pop()
        peaks.append({"height": left["height"] + 1, "hash": _h(left["hash"], right["hash"])})
    return {"peaks": peaks, "count": int(state.get("count", 0)) + 1}


def root(state: dict) -> str:
    """Bag the peaks: fold sha256 over concatenated hex, right to left."""
    peaks = [p["hash"] for p in state.get("peaks", [])]
    if not peaks:
        return EMPTY_ROOT
    acc = peaks[-1]
    for peak in reversed(peaks[:-1]):
        acc = _h(peak, acc)
    return acc


def state_from(leaves: list) -> dict:
    st = empty_state()
    for leaf in leaves:
        st = append_leaf(leaf, st)
    return st


def _peak_spans(count: int) -> list[tuple[int, int]]:
    """(start, size) leaf span of each mountain, oldest first."""
    spans, start, bit = [], 0, 1 << max(count.bit_length() - 1, 0)
    while bit:
        if count & bit:
            spans.append((start, bit))
            start += bit
        bit >>= 1
    return spans


def _node(leaves: list, lo: int, size: int) -> str:
    if size == 1:
        return leaves[lo]
    half = size // 2
    return _h(_node(leaves, lo, half), _node(leaves, lo + half, half))


def _mountain_steps(leaves: list, lo: int, size: int, position: int) -> list[dict]:
    steps: list[dict] = []
    while size > 1:
        half = size // 2
        if position < lo + half:
            steps.append({"side": "right", "hash": _node(leaves, lo + half, half)})
        else:
            steps.append({"side": "left", "hash": _node(leaves, lo, half)})
            lo += half
        size = half
    steps.reverse()  # fold order is leaf-upward
    return steps


def prove(leaves: list, position: int) -> list[dict]:
    """Inclusion proof for ``leaves[position]`` against ``root(state_from(leaves))``.

    Each step is ``{"side": "left"|"right", "hash": hex}``; verify() folds
    them onto the leaf. Steps cover the leaf's mountain path plus the peak
    bagging (right-peaks aggregate first, then each left peak).
    """
    if not 0 <= position < len(leaves):
        raise IndexError(f"position {position} outside 0..{len(leaves) - 1}")
    spans = _peak_spans(len(leaves))
    peaks = [_node(leaves, lo, size) for lo, size in spans]
    j = next(i for i, (lo, size) in enumerate(spans) if lo <= position < lo + size)
    steps = _mountain_steps(leaves, spans[j][0], spans[j][1], position)
    if j < len(peaks) - 1:
        agg = peaks[-1]
        for peak in reversed(peaks[j + 1:-1]):
            agg = _h(peak, agg)
        steps.append({"side": "right", "hash": agg})
    for peak in reversed(peaks[:j]):
        steps.append({"side": "left", "hash": peak})
    return steps


def verify(expected_root: str, leaf: str, proof: list) -> bool:
    """Fold the proof onto ``leaf``; True iff it reproduces ``expected_root``."""
    acc = str(leaf)
    for step in proof:
        if step.get("side") == "left":
            acc = _h(str(step.get("hash", "")), acc)
        else:
            acc = _h(acc, str(step.get("hash", "")))
    return acc == str(expected_root)
