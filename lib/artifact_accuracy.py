"""Deterministic accuracy instruments for generated artefacts.

Build-from-zero for the estate gap measured 2026-09-13: no IoU, SSIM,
pixel-diff or RMSE existed anywhere, so any "N% accurate" claim about a
generated artefact had no instrument and could not be made under prove-it
doctrine. This module provides the instruments — pure stdlib, deterministic,
no model calls.

Instruments:
- ``rect_iou``            — axis-aligned box intersection-over-union
- ``mask_iou``            — pixel-wise IoU on same-shape binary rasters
- ``rmse``                — root mean squared error, same-shape numeric arrays
- ``ssim``                — structural similarity on same-shape grayscale
                            arrays (8x8 window, L=255, C1/C2 defaults)
- ``pixel_accuracy``      — fraction of equal pixels

All inputs are same-shape for the raster metrics by contract; mismatched
shapes raise ValueError (fail loud, never a silent bogus score).
"""

from __future__ import annotations

import math
from typing import Sequence


def _require_same_shape(a: Sequence, b: Sequence, what: str) -> None:
    if len(a) != len(b) or any(len(ra) != len(rb) for ra, rb in zip(a, b)):
        raise ValueError(f"{what}: shapes must match, got {len(a)}x{len(a[0]) if a else 0} "
                         f"vs {len(b)}x{len(b[0]) if b else 0}")


def rect_iou(a: Sequence[float], b: Sequence[float]) -> float:
    """IoU of two axis-aligned boxes [x, y, w, h] (top-left origin)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


def mask_iou(a: Sequence[Sequence[int]], b: Sequence[Sequence[int]]) -> float:
    """Pixel-wise IoU of two binary rasters (cells truthy = foreground)."""
    _require_same_shape(a, b, "mask_iou")
    inter = 0
    union = 0
    for ra, rb in zip(a, b):
        for pa, pb in zip(ra, rb):
            fa, fb = bool(pa), bool(pb)
            inter += int(fa and fb)
            union += int(fa or fb)
    if union == 0:
        return 1.0  # both empty — vacuously identical
    return inter / union


def pixel_accuracy(a: Sequence[Sequence[int]], b: Sequence[Sequence[int]]) -> float:
    """Fraction of pixels that are exactly equal."""
    _require_same_shape(a, b, "pixel_accuracy")
    total = 0
    same = 0
    for ra, rb in zip(a, b):
        for pa, pb in zip(ra, rb):
            total += 1
            same += int(pa == pb)
    if total == 0:
        return 1.0
    return same / total


def rmse(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> float:
    """Root mean squared error over same-shape numeric rasters."""
    _require_same_shape(a, b, "rmse")
    total = 0
    sq = 0.0
    for ra, rb in zip(a, b):
        for pa, pb in zip(ra, rb):
            total += 1
            sq += (pa - pb) ** 2
    if total == 0:
        return 0.0
    return math.sqrt(sq / total)


def _window_mean_std(window: list[float]) -> tuple[float, float]:
    n = len(window)
    mean = sum(window) / n
    var = sum((v - mean) ** 2 for v in window) / (n - 1 if n > 1 else 1)
    return mean, math.sqrt(var)


def ssim(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]],
         window: int = 8, l_max: float = 255.0,
         k1: float = 0.01, k2: float = 0.03) -> float:
    """Mean structural similarity (SSIM) over sliding windows.

    Standard Wang et al. formulation on grayscale rasters; windows are
    non-overlapping (KISS) and edges are included whole. Returns 1.0 for
    identical inputs, -1.0 for maximally anticorrelated.
    """
    _require_same_shape(a, b, "ssim")
    rows, cols = len(a), len(a[0])
    if rows == 0 or cols == 0:
        raise ValueError("ssim: empty raster")
    c1 = (k1 * l_max) ** 2
    c2 = (k2 * l_max) ** 2
    scores: list[float] = []
    for r0 in range(0, rows, window):
        for c0 in range(0, cols, window):
            wa: list[float] = []
            wb: list[float] = []
            for r in range(r0, min(r0 + window, rows)):
                for c in range(c0, min(c0 + window, cols)):
                    wa.append(float(a[r][c]))
                    wb.append(float(b[r][c]))
            if len(wa) < 2:
                continue
            ma, sa = _window_mean_std(wa)
            mb, sb = _window_mean_std(wb)
            cov = sum((x - ma) * (y - mb) for x, y in zip(wa, wb)) / (len(wa) - 1)
            scores.append(
                ((2 * ma * mb + c1) * (2 * cov + c2))
                / ((ma**2 + mb**2 + c1) * (sa**2 + sb**2 + c2))
            )
    if not scores:
        return 1.0
    return sum(scores) / len(scores)
