"""lib.smt — Structural Mapping Engine kernel (Phase B of the SMT dogfood).

Submodules implement the AUDIT.md (lib/smt/AUDIT.md) ranked gap list:

  sme.py             G1 predicate representation, G2 structural consistency,
                     G3 SME stages 1-3, G4 candidate inference
  macfac.py          G5 FAC half — structural re-rank after MAC retrieval
  relational_shift.py G4/G6 alignable differences + progressive alignment
                     + the attribute→relational trajectory measure
"""
from .sme import (  # noqa: F401
    Alignment,
    CandidateInference,
    Entity,
    MatchHypothesis,
    Predicate,
    PredicateKind,
    Structure,
    build_structure,
    candidate_inferences,
    gmap,
    local_match_hypotheses,
    parallel_connectivity,
    structural_consistency,
    systematicity,
    tier_match,
)

__all__ = [
    "enabled",
    "Alignment",
    "CandidateInference",
    "Entity",
    "MatchHypothesis",
    "Predicate",
    "PredicateKind",
    "Structure",
    "build_structure",
    "candidate_inferences",
    "gmap",
    "local_match_hypotheses",
    "parallel_connectivity",
    "structural_consistency",
    "systematicity",
    "tier_match",
]

import os as _os

_OFF = frozenset({"0", "off", "false", "no"})


def enabled(feature: str) -> bool:
    """Default-on kill switch for a wired kernel feature.

    ``GRIP_SMT_<FEATURE>=0`` (or off/false/no) disables it; absent means on.
    Used by the two production seams (kono_substrate FAC re-rank, learning
    _detector relational-shift adjacency) so an operator can fall back to the
    pre-kernel behaviour without a code change.
    """
    return _os.environ.get(f"GRIP_SMT_{feature.upper()}", "1").strip().lower() not in _OFF
