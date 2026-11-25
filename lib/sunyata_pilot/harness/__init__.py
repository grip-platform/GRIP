"""Sunyata pilot harness — the H1/H2/H3 measurement lanes (paper §8).

Lane D (h1): selective correctness at matched coverage — G1 must emit
fewer fabricated definitive liabilities than G0 while sustaining the
pre-declared coverage floor, and the improvement must survive difficulty
conditioning.

Lane E (h2/h3): audit efficiency and accuracy (h2), constraint
attribution (h3).

Public surface for the rest of the fleet — a single import point; the
bin runner imports everything from ``sunyata_pilot.harness``:

    fabrications, coverage, matched_coverage_compare,
    selective_risk_curve, h1_verdict,
    h2_verdict, audit_steps, audit_accuracy,
    h3_verdict

The harness package stays dependency-free: agents/ and benchmark/ are
never imported here.
"""

from sunyata_pilot.harness.h1 import (
    REASON_FLOOR_UNMET,
    REASON_NO_IMPROVEMENT,
    REASON_NOVELTY_COLLAPSE,
    REASON_SELECTIVE_MUTENESS,
    coverage,
    fabrications,
    h1_verdict,
    matched_coverage_compare,
    selective_risk_curve,
)
from sunyata_pilot.harness.h2 import audit_accuracy, audit_steps, h2_verdict
from sunyata_pilot.harness.h3 import h3_verdict

__all__ = [
    "fabrications",
    "coverage",
    "matched_coverage_compare",
    "selective_risk_curve",
    "h1_verdict",
    "REASON_NO_IMPROVEMENT",
    "REASON_FLOOR_UNMET",
    "REASON_SELECTIVE_MUTENESS",
    "REASON_NOVELTY_COLLAPSE",
    "h2_verdict",
    "audit_steps",
    "audit_accuracy",
    "h3_verdict",
]
