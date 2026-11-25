"""De Morgan Quality Duality — Unified model bridging guardrails and converge.

By De Morgan's laws:
    NOT(ANY(violation)) === ALL(NOT(violation)) === ALL(quality_met)

Guardrails check NOT(violation_present) — reactive, blocks bad outputs.
Converge checks ALL(quality_criteria_met) — proactive, ensures good outputs.
Same logical predicate, opposite directions. This module is the bridge.

Usage:
    from quality_duality import violations_to_scores, scores_to_warnings, is_quality_met

    # Guardrail -> Converge: convert violations to dimension scores
    scores = violations_to_scores(signals, task_type="code")

    # Converge -> Guardrail: convert low scores to violation warnings
    warnings = scores_to_warnings({"correctness": 95, "design": 60, "simplicity": 85})

    # Unified check from either direction
    ok = is_quality_met(violations=signals)
    ok = is_quality_met(dimension_scores={"correctness": 90, "design": 80, "simplicity": 85})
"""

from __future__ import annotations

# Violation-to-dimension mapping: which guardrail check affects which converge dimension
VIOLATION_MAP: dict[str, dict] = {
    "DRY":    {"dimension": "design",      "cost": 15},
    "KISS":   {"dimension": "simplicity",  "cost": 15},
    "BIG-O":  {"dimension": "simplicity",  "cost": 10},
    "YAGNI":  {"dimension": "design",      "cost": 10},
    "SYNTAX": {"dimension": "correctness", "cost": 50},
}

# Reverse mapping: which principles feed each dimension
DIMENSION_PRINCIPLES: dict[str, list[str]] = {
    "correctness": ["SYNTAX"],
    "design":      ["DRY", "YAGNI"],
    "simplicity":  ["KISS", "BIG-O"],
}

# Default code dimensions (matches converge_metrics.py WEIGHT_TABLES["code"])
_CODE_DIMS = {"correctness": 0.4, "design": 0.3, "simplicity": 0.3}


def violations_to_scores(
    violations: list,
    task_type: str = "code",
) -> dict[str, int]:
    """Convert guardrail violations to converge dimension scores.

    De Morgan bridge: NOT(violation) -> quality_met.
    Each violation reduces the corresponding dimension score by its cost.
    No violations = all dimensions at 100.

    Args:
        violations: List of QualitySignal namedtuples or strings (principle names).
        task_type: Converge task type for dimension selection (default: "code").

    Returns:
        Dict mapping dimension names to scores (0-100).
    """
    # Use code dimensions by default; could load from converge_metrics if available
    dims = _CODE_DIMS
    try:
        from converge_metrics import WEIGHT_TABLES
        dims = WEIGHT_TABLES.get(task_type, _CODE_DIMS)
    except ImportError:
        pass

    scores: dict[str, int] = {dim: 100 for dim in dims}

    for v in violations:
        principle = v.principle if hasattr(v, "principle") else str(v)
        if principle in VIOLATION_MAP:
            dim = VIOLATION_MAP[principle]["dimension"]
            cost = VIOLATION_MAP[principle]["cost"]
            if dim in scores:
                scores[dim] = max(0, scores[dim] - cost)

    return scores


def scores_to_warnings(
    dimension_scores: dict[str, int | float],
    threshold: int = 70,
) -> list[str]:
    """Convert converge dimension scores to guardrail-style warnings.

    De Morgan bridge: NOT(quality_met) -> violation.
    Dimensions below threshold indicate potential violations in mapped principles.

    Args:
        dimension_scores: Dict mapping dimension names to scores (0-100).
        threshold: Score below which a warning is emitted.

    Returns:
        List of warning strings in guardrail format.
    """
    warnings: list[str] = []
    for dim, score in dimension_scores.items():
        if score < threshold and dim in DIMENSION_PRINCIPLES:
            principles = DIMENSION_PRINCIPLES[dim]
            warnings.append(
                f"[{dim.upper()} < {threshold}] Review for: {', '.join(principles)}"
            )
    return warnings


def is_quality_met(
    violations: list | None = None,
    dimension_scores: dict[str, int | float] | None = None,
    threshold: int = 70,
) -> bool:
    """Unified quality check — works from either direction.

    De Morgan: NOT(ANY(violation)) === ALL(quality >= threshold).
    Both paths must produce the same boolean answer.

    Args:
        violations: Guardrail violations (optional). Converted to scores internally.
        dimension_scores: Converge dimension scores (optional). Checked directly.
        threshold: Minimum acceptable score per dimension.

    Returns:
        True if quality is met (no significant violations / all scores above threshold).

    An explicitly EMPTY ``dimension_scores`` dict (or an empty scores map derived
    from ``violations``) is UNKNOWN, not PASS. ``all([])`` is True, and a check
    that reports "quality met" for zero measured dimensions is the same defect
    class as a converge score carrier reporting a pass with nothing checked
    (``lib/gentner_enforcer.py``) — the caller measured nothing and this must
    not silently read as "nothing was wrong". Distinguished from the
    both-arguments-omitted case below, which is a documented "no input, assume
    fine" default rather than a failed measurement.
    """
    if violations is not None:
        scores = violations_to_scores(violations)
        return bool(scores) and all(s >= threshold for s in scores.values())
    if dimension_scores is not None:
        return bool(dimension_scores) and all(
            s >= threshold for s in dimension_scores.values()
        )
    return True


def bridge_summary(
    violations: list | None = None,
    dimension_scores: dict[str, int | float] | None = None,
    threshold: int = 70,
) -> dict:
    """Generate a unified quality summary from either direction.

    Returns a dict with both representations for debugging and logging.
    """
    if violations is not None:
        scores = violations_to_scores(violations)
        warnings = scores_to_warnings(scores, threshold)
        met = bool(scores) and all(s >= threshold for s in scores.values())
    elif dimension_scores is not None:
        scores = dict(dimension_scores)
        warnings = scores_to_warnings(scores, threshold)
        met = bool(scores) and all(s >= threshold for s in scores.values())
    else:
        scores = {}
        warnings = []
        met = True

    return {
        "quality_met": met,
        "dimension_scores": scores,
        "warnings": warnings,
        "threshold": threshold,
    }
