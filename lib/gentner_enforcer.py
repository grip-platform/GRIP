"""
gentner_enforcer.py — Structural mapping enforcement gate.

Implements Gentner's Structure Mapping Theory (1983) as a mechanical
enforcement gate for any structural analogy/unification proposal in GRIP.

Not mode-specific — reusable for mode-to-wave, principle-to-gate,
system-to-system, or any future unification.

ENFORCES: validate() scores, enforce() BLOCKS violations mechanically.
Surface-level mappings (score < 40) are denied. No advisory mode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MappingRelation:
    """A claimed structural relation between source and target elements.

    source      : Element in the source domain (e.g., "research mode").
    target      : Element in the target domain (e.g., "W1 DETECTED").
    relation    : Type of relation (causal, orthogonal, surface, meta).
    justification : Why this mapping is structurally valid.
    """
    source: str
    target: str
    relation: str = "causal"
    justification: str = ""


@dataclass
class MappingProposal:
    """A complete structural mapping proposal for Gentner validation.

    source_domain   : Name of the source system (e.g., "GRIP modes").
    target_domain   : Name of the target system (e.g., "Fibonacci wave protocol").
    source_elements : List of elements in the source domain.
    target_elements : List of elements in the target domain.
    relations       : Claimed mappings between source and target elements.
    unmapped_source : Source elements explicitly declared as not mapping.
    unmapped_target : Target elements explicitly declared as not mapping.
    """
    source_domain: str = ""
    target_domain: str = ""
    source_elements: list[str] = field(default_factory=list)
    target_elements: list[str] = field(default_factory=list)
    relations: list[MappingRelation] = field(default_factory=list)
    unmapped_source: list[str] = field(default_factory=list)
    unmapped_target: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    """Result of a single Gentner check."""
    name: str
    passed: bool
    score: float  # 0-100
    message: str = ""
    details: list[str] = field(default_factory=list)


@dataclass
class GentnerScore:
    """Aggregate validation result from all Gentner checks.

    total_score : Weighted average of all check scores (0-100).
    checks      : Individual check results.
    violations  : Structural violations detected (blocking if score < threshold).
    """
    total_score: float = 0.0
    checks: list[CheckResult] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when nothing here recorded a failure.

        Derived from the FAILURE set rather than from ``violations``, because
        those are not the same list. ``validate`` appends every check's
        ``details`` to ``violations`` whether or not that check passed
        (see the collection loop below), so a clean proposal whose checks
        carry advisory detail has a non-empty ``violations`` and has failed
        nothing.

        The empty-``checks`` case is the one this property used to get wrong.
        ``all([])`` is True, so a score built as a CARRIER for a verdict decided
        elsewhere -- the decision-field gate constructs exactly that, with
        ``total_score=0.0`` and a populated ``violations`` -- reported a pass
        while denying. No caller read it there, so nothing was mis-gated; it
        was a value waiting to be believed. With no checks to consult, the only
        failure signal such a score holds is ``violations``.
        """
        if any(not c.passed for c in self.checks):
            return False
        if self.checks:
            return True
        return not self.violations

    @property
    def check_names(self) -> list[str]:
        return [c.name for c in self.checks]

    @property
    def warnings(self) -> list[str]:
        """Non-blocking advisory view of every issue this score carries.

        Derived (no extra state): the structural ``violations`` plus the failure
        detail of each individual check that did not pass. A surface/weak proposal
        therefore reports >=1 warning even when its total_score sits above the
        hard BLOCK threshold — the advisory surface complements the binary gate.
        """
        out = list(self.violations)
        out.extend(c.message for c in self.checks if not c.passed and c.message)
        return out


# ── Check weights ─────────────────────────────────────────────────────────

CHECK_WEIGHTS = {
    "causal_relations": 0.30,
    "orthogonality": 0.20,
    "over_mapping": 0.20,
    "under_mapping": 0.15,
    "surface_analogy": 0.15,
}


# ── Individual checks ─────────────────────────────────────────────────────

def check_causal_relations(proposal: MappingProposal) -> CheckResult:
    """Verify that mapped relations are causal/structural, not just surface.

    Gentner's key insight: good analogies map relations (especially causal),
    not just attributes. A mapping claiming "research -> W1" must explain
    the causal chain, not just "both are first."
    """
    if not proposal.relations:
        return CheckResult(
            name="causal_relations",
            passed=False,
            score=0.0,
            message="No relations defined in proposal",
        )

    causal_count = 0
    surface_count = 0
    details = []

    for rel in proposal.relations:
        if rel.relation == "causal" and rel.justification:
            causal_count += 1
        elif rel.relation == "surface" or not rel.justification:
            surface_count += 1
            details.append(f"Surface/unjustified: {rel.source} -> {rel.target}")

    total = len(proposal.relations)
    causal_ratio = causal_count / total if total else 0

    passed = causal_ratio >= 0.6
    score = min(100, causal_ratio * 100)

    return CheckResult(
        name="causal_relations",
        passed=passed,
        score=score,
        message=f"{causal_count}/{total} relations are causal ({causal_ratio:.0%})",
        details=details,
    )


def check_orthogonality(proposal: MappingProposal) -> CheckResult:
    """Verify that unmapped elements are genuinely orthogonal.

    Elements declared as "not mapping" must be orthogonal to the target
    domain (modifiers, meta-operations), not just unclassified leftovers.
    """
    unmapped = proposal.unmapped_source
    total_source = len(proposal.source_elements)

    if total_source == 0:
        return CheckResult(
            name="orthogonality",
            passed=True,
            score=100.0,
            message="No source elements to check",
        )

    mapped_sources = {r.source for r in proposal.relations}
    declared_unmapped = set(unmapped)
    actual_unmapped = set(proposal.source_elements) - mapped_sources

    # Check: every unmapped element should be declared as such
    undeclared = actual_unmapped - declared_unmapped
    details = []
    if undeclared:
        details.append(f"Unmapped but not declared: {sorted(undeclared)}")

    # Check: no element is both mapped and declared unmapped
    contradictions = mapped_sources & declared_unmapped
    if contradictions:
        details.append(f"Both mapped and unmapped: {sorted(contradictions)}")

    issues = len(undeclared) + len(contradictions)
    score = max(0, 100 - issues * 20)
    passed = issues == 0

    return CheckResult(
        name="orthogonality",
        passed=passed,
        score=score,
        message=f"{len(declared_unmapped)} elements declared orthogonal/unmapped",
        details=details,
    )


def check_over_mapping(proposal: MappingProposal) -> CheckResult:
    """Detect over-mapping: too many source elements forced into mappings.

    Gentner warns against mapping everything — some elements are genuinely
    independent. If > 80% of source elements are mapped, the mapping may
    be over-reaching.
    """
    total_source = len(proposal.source_elements)
    if total_source == 0:
        return CheckResult(
            name="over_mapping",
            passed=True,
            score=100.0,
            message="No source elements",
        )

    mapped_count = len({r.source for r in proposal.relations})
    mapped_ratio = mapped_count / total_source

    details = []

    # Over-mapping threshold: > 80% mapped is suspicious
    if mapped_ratio > 0.80:
        details.append(
            f"{mapped_ratio:.0%} of source elements mapped — "
            "may indicate forced mappings"
        )

    # Also check: multiple sources to same target (many-to-one)
    target_counts: dict[str, int] = {}
    for rel in proposal.relations:
        target_counts[rel.target] = target_counts.get(rel.target, 0) + 1

    heavy_targets = {t: c for t, c in target_counts.items() if c > 3}
    if heavy_targets:
        for t, c in heavy_targets.items():
            details.append(f"Target '{t}' has {c} sources — check if forced")

    # Score: 40-60% mapped is ideal (Gentner), penalize extremes
    if 0.30 <= mapped_ratio <= 0.70:
        score = 100.0
    elif mapped_ratio < 0.30:
        score = max(50, mapped_ratio / 0.30 * 100)
    else:
        score = max(30, (1.0 - mapped_ratio) / 0.20 * 100)

    passed = mapped_ratio <= 0.80 and not heavy_targets

    return CheckResult(
        name="over_mapping",
        passed=passed,
        score=score,
        message=f"{mapped_count}/{total_source} source elements mapped ({mapped_ratio:.0%})",
        details=details,
    )


def check_under_mapping(proposal: MappingProposal) -> CheckResult:
    """Detect under-mapping: too few mappings to be structurally useful.

    If < 20% of source elements are mapped, the analogy may be too weak
    to provide structural insight.
    """
    total_source = len(proposal.source_elements)
    if total_source == 0:
        return CheckResult(
            name="under_mapping",
            passed=True,
            score=100.0,
            message="No source elements",
        )

    mapped_count = len({r.source for r in proposal.relations})
    mapped_ratio = mapped_count / total_source

    details = []
    if mapped_ratio < 0.20:
        details.append(
            f"Only {mapped_ratio:.0%} mapped — analogy may be too weak"
        )

    passed = mapped_ratio >= 0.20
    score = min(100, mapped_ratio / 0.20 * 100) if mapped_ratio < 0.20 else 100.0

    return CheckResult(
        name="under_mapping",
        passed=passed,
        score=score,
        message=f"{mapped_count}/{total_source} source elements mapped ({mapped_ratio:.0%})",
        details=details,
    )


def check_surface_analogy(proposal: MappingProposal) -> CheckResult:
    """Detect surface-level analogies masquerading as structural ones.

    Surface analogies map attributes (both are "first", both are "big"),
    not relations (A causes B, A is higher-order than B).

    Heuristic: relations with relation="surface" or empty justification.
    """
    if not proposal.relations:
        return CheckResult(
            name="surface_analogy",
            passed=True,
            score=100.0,
            message="No relations to check",
        )

    surface_count = 0
    details = []

    for rel in proposal.relations:
        if rel.relation == "surface":
            surface_count += 1
            details.append(f"Surface: {rel.source} -> {rel.target}")
        elif not rel.justification:
            surface_count += 1
            details.append(f"No justification: {rel.source} -> {rel.target}")

    total = len(proposal.relations)
    surface_ratio = surface_count / total if total else 0

    passed = surface_ratio <= 0.30
    score = max(0, 100 - surface_ratio * 100)

    return CheckResult(
        name="surface_analogy",
        passed=passed,
        score=score,
        message=f"{surface_count}/{total} surface-level relations ({surface_ratio:.0%})",
        details=details,
    )


# ── Main validator ─────────────────────────────────────────────────────────

# ── Enforcement thresholds ───────────────────────────────────────────────

# Score below this = surface-level mapping, BLOCKED mechanically
ENFORCEMENT_THRESHOLD_BLOCK = 40

# Score below this = structural issues, BLOCKED mechanically
ENFORCEMENT_THRESHOLD_WEAK = 60


@dataclass
class EnforcementResult:
    """Gate-compatible enforcement result.

    decision : "deny" or "allow"
    reason   : Human-readable explanation
    score    : The Gentner validation score (for logging)
    violations : List of specific structural violations
    """
    decision: str  # "deny" or "allow"
    reason: str
    score: GentnerScore
    violations: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.decision == "deny"


def validate(proposal: MappingProposal) -> GentnerScore:
    """Run all 5 Gentner checks on a mapping proposal.

    Returns a GentnerScore with individual results and violations.
    Use enforce() for gate-compatible deny/allow decisions.
    """
    checks = [
        check_causal_relations(proposal),
        check_orthogonality(proposal),
        check_over_mapping(proposal),
        check_under_mapping(proposal),
        check_surface_analogy(proposal),
    ]

    # Weighted total score
    total = 0.0
    for check in checks:
        weight = CHECK_WEIGHTS.get(check.name, 0.2)
        total += check.score * weight

    # Collect violations from failed checks
    violations = []
    for check in checks:
        if not check.passed:
            violations.append(f"[{check.name}] {check.message}")
        for detail in check.details:
            violations.append(f"  {detail}")

    score = GentnerScore(
        total_score=round(total, 1),
        checks=checks,
        violations=violations,
    )

    logger.info("[gentner] Validation score: %.1f/100 (%s)",
                score.total_score, "PASS" if score.passed else "VIOLATION")

    return score


def enforce(proposal: MappingProposal) -> EnforcementResult:
    """Mechanical enforcement gate for structural mapping proposals.

    BLOCKS proposals that fail Gentner validation:
    - Score < 40: Surface-level mapping (DENY)
    - Score < 60: Significant structural issues (DENY)
    - Score >= 60: Structural mapping acceptable (ALLOW)

    This is GRIP's mechanical guardrail — not advisory, not suggestive.
    The decision is deterministic based on the score threshold.
    """
    # Empty proposals are denied mechanically — nothing to validate means
    # nothing proven, not "everything fine"
    if not proposal.relations and not proposal.source_elements:
        empty_score = GentnerScore(
            total_score=0.0,
            checks=[],
            violations=["Empty proposal — no relations or elements defined"],
        )
        return EnforcementResult(
            decision="deny",
            reason=(
                "[GUARDRAIL: STRUCTURAL MAPPING] Empty proposal — "
                "no relations or elements defined. Cannot validate."
            ),
            score=empty_score,
            violations=["Empty proposal — no relations or elements defined"],
        )

    score = validate(proposal)

    if score.total_score < ENFORCEMENT_THRESHOLD_BLOCK:
        return EnforcementResult(
            decision="deny",
            reason=(
                f"[GUARDRAIL: STRUCTURAL MAPPING] Score {score.total_score:.0f}/100 "
                f"(threshold: {ENFORCEMENT_THRESHOLD_BLOCK}). "
                f"Mapping is surface-level — relations are based on attributes, "
                f"not causal structure. {len(score.violations)} violations detected."
            ),
            score=score,
            violations=score.violations,
        )

    if score.total_score < ENFORCEMENT_THRESHOLD_WEAK:
        return EnforcementResult(
            decision="deny",
            reason=(
                f"[GUARDRAIL: STRUCTURAL MAPPING] Score {score.total_score:.0f}/100 "
                f"(threshold: {ENFORCEMENT_THRESHOLD_WEAK}). "
                f"Mapping has significant structural issues. "
                f"{len(score.violations)} violations: "
                + "; ".join(v for v in score.violations if not v.startswith("  "))
            ),
            score=score,
            violations=score.violations,
        )

    return EnforcementResult(
        decision="allow",
        reason=f"Structural mapping validated: {score.total_score:.0f}/100",
        score=score,
        violations=score.violations,
    )


def _is_our_bug(exc: BaseException) -> bool:
    """True when the external scorer failed because WE called it wrongly.

    The scorer re-raises call-shape errors on purpose
    (``external_smt_scorer._is_call_shape_error``) so they surface; re-burying
    them one level up leaves the council silently unrun. That is how #4411 hid
    THREE stacked bugs -- a missing argument, then ``.get()`` on a dataclass,
    then a wrong field name -- each visible only once the previous was fixed.

    ``ImportError`` joined the list in #4573: renaming or deleting
    ``score_mapping_external`` raises it from the import statement itself, and
    it matched neither branch, so a single typo degraded on one warning line.

    ``ModuleNotFoundError`` is carved out. It subclasses ``ImportError`` but
    means the scorer is not installed at all -- an absent optional dependency,
    so an operating condition. Without the carve-out, every deployment lacking
    the scorer turns from a graceful fallback into a crash.
    """
    if isinstance(exc, ModuleNotFoundError):
        return False
    return isinstance(exc, (ImportError, TypeError, AttributeError, NameError))


def enforce_external(proposal: MappingProposal, mode: str = "standard") -> EnforcementResult:
    """Enforcement gate with external council scoring.

    Runs mechanical enforce() first. If it passes, sends to external
    SMT council for independent scoring. The producing model never
    scores its own work.

    Falls back to mechanical-only when a provider is unavailable -- but NOT
    when this module calls the scorer wrongly. A provider outage is an
    operating condition and degrading is correct; a wrong call shape is our
    bug, and hiding it behind one warning line is what let half this council
    go unrun since it shipped (#4411, #4502).
    """
    mechanical_result = enforce(proposal)

    if mechanical_result.blocked:
        return mechanical_result

    try:
        from external_smt_scorer import score_mapping_external
        external = score_mapping_external(proposal, mode=mode)
        return EnforcementResult(
            decision=external.decision,
            reason=external.reason,
            score=mechanical_result.score,
            violations=mechanical_result.violations,
        )
    except Exception as e:
        if _is_our_bug(e):
            raise  # see _is_our_bug: surfacing beats a silently unrun council
        # A real outage. Degrading to mechanical-only is the fallback's job.
        logger.warning("[gentner] External scoring unavailable: %s", e)
        return mechanical_result


# ---------------------------------------------------------------------------
# Decision-anatomy SMT gate (Decision Anatomy, Session A)
# ---------------------------------------------------------------------------
# The decision-notarisation relation is itself a structural mapping: a field is
# admissible on a delegated IDR's anatomy ONLY if it maps to that relation. This
# is the SAME Gentner discipline the mapping gate enforces — surface grafts
# (tone / persona / "personality"; confidence-as-criterion) are relational
# impostors and are DENIED. So the gentner-enforcer is the single structural-
# admissibility authority for BOTH mapping proposals AND decision-anatomy fields.
#
# DRY: this delegates to lib.decision_record.reject_surface_fields — the one
# canonical SMT validator over decision fields — rather than re-listing the
# admissible/graft sets here. The gentner-enforcer is the documented home; the
# validator is the implementation, so the two can never drift.


def decision_field_gate(raw: dict) -> EnforcementResult:
    """SMT gate over a delegated decision's anatomy fields (structural mapping
    to the decision-notarisation relation).

    ALLOW iff every key maps to that relation (the admissible decision-anatomy
    fields). DENY any surface graft (tone/persona/personality, or confidence
    used as a merge/ratify criterion) or non-admissible key — mechanically, not
    advisorily, exactly like the mapping gate.

    Pure validator (no I/O, no LLM): delegates to the canonical
    ``lib.decision_record.reject_surface_fields`` so there is ONE source of
    structural-admissibility truth for decision fields. ``DecisionError`` from
    that validator becomes a blocked ``EnforcementResult`` carrying the reason.
    """
    try:
        from lib.decision_record import DecisionError, reject_surface_fields
    except ImportError as e:  # non-GRIP context only — fail-safe DENY (no validator)
        v = [f"decision-field SMT gate unavailable: {e}"]
        return EnforcementResult(
            decision="deny",
            reason=f"[GUARDRAIL: STRUCTURAL MAPPING] {v[0]}",
            score=GentnerScore(total_score=0.0, checks=[], violations=v),
            violations=v,
        )
    # Catch ONLY DecisionError (surface graft / non-admissible key). An unrelated
    # runtime error inside the validator (e.g. an AttributeError from a future bug)
    # is NOT a "this field is inadmissible" verdict — it must PROPAGATE so the
    # breakage surfaces, never be laundered into a synthetic clean deny (AGORA #3636
    # Speaker C, valid concern — verified against the diff, not the truncated line).
    try:
        reject_surface_fields(raw or {})
    except DecisionError as e:  # the field set is structurally inadmissible
        v = [str(e)]
        return EnforcementResult(
            decision="deny",
            reason=f"[GUARDRAIL: STRUCTURAL MAPPING] {v[0]}",
            score=GentnerScore(total_score=0.0, checks=[], violations=v),
            violations=v,
        )
    return EnforcementResult(
        decision="allow",
        reason="all fields map to the decision-notarisation relation",
        score=GentnerScore(total_score=100.0, checks=[], violations=[]),
        violations=[],
    )

