#!/usr/bin/env python3
"""macfac.py — FAC half of MAC/FAC (Forbus, Gentner & Law 1995).

The MAC half already exists in GRIP (sqlite-vec KNN + FTS5 + RRF fusion,
lib/embeddings/search.py + lib/kono_substrate.py). This module supplies the
missing FAC: after MAC returns a candidate list, re-rank it by STRUCTURAL
alignment against the query structure, then fuse.

Seam: `kono_substrate._finalize_results` — wired there behind `GRIP_SMT_FAC`
(default on): the top window of MAC hits is re-ranked before attestation. The
fusion stays deterministic and dependency-free so it can sit on any retrieval
pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .sme import (
    Alignment,
    Structure,
    candidate_inferences,
    gmap,
    structural_consistency,
    systematicity,
)

__all__ = ["FacResult", "fac_rerank", "structural_score", "fuse"]


@dataclass(frozen=True)
class FacResult:
    item_id: str
    mac_score: float
    structural_score: float
    fused_score: float
    alignment_size: int
    systematicity: float
    candidate_inferences: int


def structural_score(query: Structure, candidate: Structure) -> tuple[float, Alignment]:
    """FAC alignment score in [0, 1] + the alignment that produced it.

    Normalises the GMap score by the query's own predicate weight so a
    fully-aligned candidate scores 1.0 regardless of size.
    """
    a = gmap(query, candidate)
    if a.size == 0:
        return 0.0, a
    q_total = sum(
        3.0 if p.kind == "higher_order" else 2.0 if p.kind == "relation" else 1.0
        for p in query.predicates
    ) + len(query.entities)
    if q_total <= 0:
        return 0.0, a
    raw = min(a.score / q_total, 1.0)
    # systematicity scales the score: unconnected matches are worth less
    return round(raw * (0.5 + 0.5 * min(systematicity(query, candidate, a), 1.0)), 6), a


def fac_rerank(
    query: Structure,
    candidates: Iterable[tuple[str, float, Structure]],
    top_n: int = 10,
    alpha: float = 0.6,
) -> list[FacResult]:
    """Re-rank MAC candidates by structural alignment, fused score.

    alpha balances MAC (vector) evidence vs FAC (structural) evidence.
    Deterministic; equal fused scores keep MAC order (stable).
    """
    scored: list[FacResult] = []
    for item_id, mac, struct in candidates:
        s, a = structural_score(query, struct)
        scored.append(FacResult(
            item_id=item_id,
            mac_score=round(float(mac), 6),
            structural_score=s,
            fused_score=round(alpha * float(mac) + (1 - alpha) * s, 6),
            alignment_size=a.size,
            systematicity=round(systematicity(query, struct, a), 6),
            candidate_inferences=len(candidate_inferences(query, struct, a)),
        ))
    return sorted(scored, key=lambda r: (-r.fused_score, -r.mac_score, r.item_id))[:top_n]


def fuse(mac_score: float, structural_score: float, alpha: float = 0.6) -> float:
    """Weighted fusion of a MAC score and an FAC structural score."""
    return alpha * mac_score + (1 - alpha) * structural_score
