#!/usr/bin/env python3
"""
semantic_criterion.py -- Natural language criteria for /converge.

Translates human-readable criteria like "tests pass and code is clean" into
structured CriterionSpec objects that produce callable criterion functions
compatible with converge().

No LLM calls -- pure keyword mapping with configurable thresholds.

Usage:
    from lib.semantic_criterion import parse_criterion, to_criterion_fn

    spec = parse_criterion("tests pass and code is clean")
    criterion = to_criterion_fn(spec)

    # Use with converge()
    result = converge(state, criterion, improve, max_depth=5)

    # Or inspect the spec
    print(spec.dimensions)   # ['correctness', 'design']
    print(spec.thresholds)   # {'correctness': 90, 'design': 80}
    print(spec.logical_op)   # 'all'
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# =========================================================================
# Keyword-to-dimension mapping
# =========================================================================

# Each entry: keyword/phrase -> (dimension_name, threshold)
# Dimensions align with converge_metrics.py task types:
#   code:        correctness, design, simplicity
#   explanation: clarity, accuracy, depth
#   design:      completeness, coherence, elegance
# And principles_criterion.py:
#   dry_score, kiss_score, solid_score, grasp_score, yagni_score, ysh_score

KEYWORD_MAP: dict[str, tuple[str, float]] = {
    # Correctness / testing
    "tests pass":           ("correctness", 90),
    "tests passing":        ("correctness", 90),
    "tests green":          ("correctness", 90),
    "correct":              ("correctness", 85),
    "correctness":          ("correctness", 85),
    "no bugs":              ("correctness", 90),
    "bug free":             ("correctness", 90),
    "bug-free":             ("correctness", 90),
    "works":                ("correctness", 80),
    "functional":           ("correctness", 80),

    # Design / architecture
    "clean code":           ("design", 80),
    "clean":                ("design", 80),
    "well designed":        ("design", 85),
    "well-designed":        ("design", 85),
    "good design":          ("design", 85),
    "good architecture":    ("design", 85),
    "maintainable":         ("design", 80),
    "readable":             ("design", 80),
    "design":               ("design", 80),

    # Simplicity
    "simple":               ("simplicity", 85),
    "simplicity":           ("simplicity", 85),
    "minimal":              ("simplicity", 85),
    "concise":              ("simplicity", 80),
    "not complex":          ("simplicity", 80),
    "straightforward":      ("simplicity", 80),
    "easy to understand":   ("simplicity", 85),

    # Completeness
    "complete":             ("completeness", 90),
    "completeness":         ("completeness", 90),
    "comprehensive":        ("completeness", 85),
    "thorough":             ("completeness", 85),
    "all cases":            ("completeness", 90),
    "all edge cases":       ("completeness", 90),
    "edge cases handled":   ("completeness", 90),
    "edge cases":           ("completeness", 85),
    "nothing missing":      ("completeness", 90),

    # Documentation
    "documented":           ("completeness", 85),
    "documentation":        ("completeness", 85),
    "docs complete":        ("completeness", 90),
    "well documented":      ("completeness", 90),
    "well-documented":      ("completeness", 90),

    # Clarity (for explanations)
    "clear":                ("clarity", 85),
    "clarity":              ("clarity", 85),
    "understandable":       ("clarity", 80),
    "well explained":       ("clarity", 85),
    "well-explained":       ("clarity", 85),

    # Accuracy
    "accurate":             ("accuracy", 90),
    "accuracy":             ("accuracy", 90),
    "precise":              ("accuracy", 85),
    "no errors":            ("accuracy", 90),

    # Depth
    "deep":                 ("depth", 80),
    "depth":                ("depth", 80),
    "in-depth":             ("depth", 85),
    "detailed":             ("depth", 80),

    # Coherence
    "coherent":             ("coherence", 85),
    "coherence":            ("coherence", 85),
    "consistent":           ("coherence", 80),
    "unified":              ("coherence", 80),

    # Elegance
    "elegant":              ("elegance", 85),
    "elegance":             ("elegance", 85),
    "beautiful":            ("elegance", 80),
    "polished":             ("elegance", 80),

    # Design principles (from principles_criterion.py)
    "dry":                  ("dry_score", 90),
    "no duplication":       ("dry_score", 90),
    "no duplicate":         ("dry_score", 85),
    "no duplicates":        ("dry_score", 85),
    "no dry violations":    ("dry_score", 90),

    "kiss":                 ("kiss_score", 85),
    "not over-engineered":  ("kiss_score", 85),
    "not overengineered":   ("kiss_score", 85),

    "solid":                ("solid_score", 85),
    "solid principles":     ("solid_score", 85),
    "low coupling":         ("solid_score", 85),
    "decoupled":            ("solid_score", 85),

    "grasp":                ("grasp_score", 80),
    "single responsibility": ("grasp_score", 85),

    "yagni":                ("yagni_score", 85),
    "no dead code":         ("yagni_score", 90),
    "no unused code":       ("yagni_score", 90),

    "ysh":                  ("ysh_score", 80),
    "patterns abstracted":  ("ysh_score", 85),

    # Quality catch-alls
    "high quality":         ("design", 85),
    "production ready":     ("correctness", 90),
    "production-ready":     ("correctness", 90),
    "ready":                ("completeness", 80),
}


# =========================================================================
# CriterionSpec dataclass
# =========================================================================

@dataclass
class CriterionSpec:
    """Structured representation of a natural language criterion.

    Attributes:
        dimensions: List of dimension names extracted from the text.
        thresholds: Mapping of dimension name to minimum score (0-100).
        logical_op: 'all' (every dimension must pass) or 'any' (at least one).
        raw_text: The original natural language input.
    """
    dimensions: list[str] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=dict)
    logical_op: str = "all"  # 'all' or 'any'
    raw_text: str = ""

    def __repr__(self) -> str:
        op = " AND " if self.logical_op == "all" else " OR "
        parts = [f"{dim}>={self.thresholds.get(dim, 80)}" for dim in self.dimensions]
        return f"CriterionSpec({op.join(parts)})"


# =========================================================================
# Negation patterns
# =========================================================================

# "no X violations" / "no X" patterns boost the mapped threshold
_NEGATION_PREFIXES = re.compile(
    r"(?:no|zero|without|free of|eliminate|avoid)\s+",
    re.IGNORECASE,
)

# Negation phrases that map to a principle with a boosted threshold
_NEGATION_MAP: dict[str, tuple[str, float]] = {
    "violations":       ("design", 90),
    "bugs":             ("correctness", 95),
    "errors":           ("accuracy", 95),
    "complexity":       ("simplicity", 90),
    "duplication":      ("dry_score", 90),
    "dead code":        ("yagni_score", 90),
    "unused code":      ("yagni_score", 90),
    "coupling":         ("solid_score", 90),
}


# =========================================================================
# Parsing
# =========================================================================

def _normalise(text: str) -> str:
    """Lowercase, strip, collapse whitespace."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _split_clauses(text: str) -> tuple[list[str], str]:
    """Split text on 'and'/'or' connectives, return clauses and logical op.

    Returns:
        (clauses, logical_op) where logical_op is 'all' or 'any'.
        If both 'and' and 'or' appear, 'and' takes precedence (safer).
    """
    has_and = bool(re.search(r"\band\b", text))
    has_or = bool(re.search(r"\bor\b", text))

    if has_or and not has_and:
        logical_op = "any"
        clauses = re.split(r"\bor\b", text)
    elif has_and:
        logical_op = "all"
        clauses = re.split(r"\band\b", text)
    else:
        # Single clause
        logical_op = "all"
        clauses = [text]

    return [c.strip() for c in clauses if c.strip()], logical_op


def _match_clause(clause: str) -> Optional[tuple[str, float]]:
    """Match a single clause against the keyword map.

    Tries longest match first to avoid partial matches
    (e.g. "clean code" before "clean").

    Returns:
        (dimension, threshold) or None if no match.
    """
    clause = _normalise(clause)

    # Check negation patterns first
    neg_match = _NEGATION_PREFIXES.match(clause)
    if neg_match:
        remainder = clause[neg_match.end():]
        # Check negation-specific map
        for neg_key, (dim, thresh) in _NEGATION_MAP.items():
            if neg_key in remainder:
                return (dim, thresh)
        # Also check if the remainder itself is in the keyword map
        # but boost threshold by 5 (negation implies stricter)
        for keyword in sorted(KEYWORD_MAP, key=len, reverse=True):
            if keyword in remainder:
                dim, base_thresh = KEYWORD_MAP[keyword]
                return (dim, min(100, base_thresh + 5))

    # Direct keyword matching (longest first)
    for keyword in sorted(KEYWORD_MAP, key=len, reverse=True):
        if keyword in clause:
            return KEYWORD_MAP[keyword]

    return None


def parse_criterion(text: str) -> CriterionSpec:
    """Parse natural language into a structured CriterionSpec.

    Supports:
        - Single criteria: "tests pass"
        - Compound criteria: "tests pass and code is clean"
        - Or-criteria: "simple or elegant"
        - Negation: "no DRY violations"
        - Unknown keywords produce a default dimension with threshold 80

    Args:
        text: Natural language criterion string.

    Returns:
        CriterionSpec with extracted dimensions, thresholds, and logical op.
    """
    if not text or not text.strip():
        return CriterionSpec(
            dimensions=["completeness"],
            thresholds={"completeness": 80},
            logical_op="all",
            raw_text=text,
        )

    normalised = _normalise(text)
    clauses, logical_op = _split_clauses(normalised)

    dimensions: list[str] = []
    thresholds: dict[str, float] = {}

    for clause in clauses:
        match = _match_clause(clause)
        if match:
            dim, thresh = match
            if dim not in thresholds:
                dimensions.append(dim)
                thresholds[dim] = thresh
            else:
                # Same dimension mentioned twice -- use the higher threshold
                thresholds[dim] = max(thresholds[dim], thresh)
        else:
            # Unknown clause -- add a generic completeness check
            if "completeness" not in thresholds:
                dimensions.append("completeness")
                thresholds["completeness"] = 80

    return CriterionSpec(
        dimensions=dimensions,
        thresholds=thresholds,
        logical_op=logical_op,
        raw_text=text,
    )


# =========================================================================
# Criterion function generation
# =========================================================================

def _make_check(dim: str, minimum: float) -> Callable[[Any], bool]:
    """Create a single dimension check function."""
    def check(state: Any) -> bool:
        if isinstance(state, dict):
            return state.get(dim, 0) >= minimum
        return getattr(state, dim, 0) >= minimum
    check.__name__ = f"{dim}>={minimum}"
    return check


def to_criterion_fn(spec: CriterionSpec) -> Callable[[Any], bool]:
    """Convert a CriterionSpec to a callable criterion function.

    The returned function is compatible with converge():
        criterion(state) -> bool

    The state can be a dict or an object with attributes matching
    the dimension names.

    Args:
        spec: A CriterionSpec from parse_criterion().

    Returns:
        A callable that takes state and returns True when the criterion is met.
    """
    if not spec.dimensions:
        # Empty spec -- always passes
        return lambda state: True

    checks = [_make_check(dim, spec.thresholds[dim]) for dim in spec.dimensions]

    if spec.logical_op == "any":
        def any_criterion(state: Any) -> bool:
            return any(c(state) for c in checks)
        any_criterion.__name__ = f"semantic_any({len(checks)})"
        return any_criterion
    else:
        def all_criterion(state: Any) -> bool:
            return all(c(state) for c in checks)
        all_criterion.__name__ = f"semantic_all({len(checks)})"
        return all_criterion


# =========================================================================
# Convenience: parse + build in one call
# =========================================================================

def semantic_criterion(text: str) -> Callable[[Any], bool]:
    """Parse natural language and return a criterion function directly.

    Shorthand for: to_criterion_fn(parse_criterion(text))

    Args:
        text: Natural language criterion string.

    Returns:
        A callable criterion function for converge().
    """
    return to_criterion_fn(parse_criterion(text))


# =========================================================================
# CLI: quick test / introspection
# =========================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 lib/semantic_criterion.py '<criterion text>'")
        print()
        print("Examples:")
        print("  python3 lib/semantic_criterion.py 'tests pass and code is clean'")
        print("  python3 lib/semantic_criterion.py 'simple or elegant'")
        print("  python3 lib/semantic_criterion.py 'no DRY violations'")
        sys.exit(1)

    text = " ".join(sys.argv[1:])
    spec = parse_criterion(text)
    print(f"Input:      {spec.raw_text}")
    print(f"Dimensions: {spec.dimensions}")
    print(f"Thresholds: {spec.thresholds}")
    print(f"Logical op: {spec.logical_op}")
    print(f"Repr:       {spec!r}")
    print()

    # Quick test with a sample state
    fn = to_criterion_fn(spec)
    sample = {d: 100 for d in spec.dimensions}
    print(f"Test (all 100): {fn(sample)}")
    sample_low = {d: 50 for d in spec.dimensions}
    print(f"Test (all  50): {fn(sample_low)}")
