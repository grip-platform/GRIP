"""
external_smt_scorer.py -- External multi-LLM council scoring for structural mappings.

Routes Gentner SMT proposals through external providers (Gemini, Llama, Qwen)
with zero system instructions. The producing model NEVER scores its own work.

Mechanical gates (gentner_enforcer) remain the deterministic floor.
External council provides the numeric score for fitness/acceptance.

Origin: Gen 488. V>> directive: "never self-score via the model that produced the work."
"""

import inspect
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
    sys.path.insert(0, str(Path(__file__).resolve().parent))

logger = logging.getLogger(__name__)

SMT_WEIGHT_TABLE = {
    "causal_structure": 0.35,
    "mapping_completeness": 0.25,
    "systematicity": 0.20,
    "falsifiability": 0.20,
}

# The task description handed to every council leg. One constant so the two
# legs cannot drift into describing different tasks to their providers.
SMT_COUNCIL_TASK = "Gentner SMT validation"

# Legs the council is designed to consult. A run that consults fewer than this
# is degraded, and must not report itself as externally validated.
COUNCIL_QUORUM = 2


class ProviderUnavailable(RuntimeError):
    """A council leg could not supply a score.

    An operating condition (provider down, nobody answered), never a bug in
    how this module calls it -- the two are handled differently below.
    """


@dataclass
class ExternalSmtVerdict:
    """Combined mechanical + external council verdict for a mapping."""
    mechanical_score: float = 0.0
    council_score: float = 0.0
    council_providers: int = 0
    providers_attempted: int = 0
    council_provider_scores: dict = field(default_factory=dict)
    combined_score: float = 0.0
    decision: str = "deny"
    externally_validated: bool = False
    reason: str = ""


def serialise_proposal(proposal) -> str:
    """Serialise a MappingProposal to provider-agnostic text.

    No system instructions. Pure domain description for external review.
    """
    lines = [
        f"Source domain: {proposal.source_domain}",
        f"Target domain: {proposal.target_domain}",
        "",
        "Claimed structural relations:",
    ]
    for r in proposal.relations:
        lines.append(f"  {r.source} -> {r.target} [{r.relation}]: {r.justification}")
    if proposal.unmapped_source:
        lines.append(f"\nUnmapped source elements: {', '.join(proposal.unmapped_source)}")
    if proposal.unmapped_target:
        lines.append(f"Unmapped target elements: {', '.join(proposal.unmapped_target)}")
    mapped = len(proposal.relations)
    total = len(proposal.source_elements) + len(proposal.target_elements)
    if total > 0:
        lines.append(f"\nMapping coverage: {mapped}/{total} elements ({100*mapped/total:.0f}%)")
    return "\n".join(lines)


def _combine_scores(mechanical_score: float, council_score: float) -> tuple[float, str]:
    """Combine mechanical gate + council score into final decision.

    Mechanical DENY (<60) overrides council. Council <50 vetoes mechanical pass.
    Weighting: 30% mechanical + 70% council when both pass.
    """
    if mechanical_score < 60:
        return mechanical_score, "deny"
    if council_score < 50:
        return council_score, "deny"
    combined = 0.3 * mechanical_score + 0.7 * council_score
    return round(combined, 1), "allow"


def _query_external_reviewer(text: str) -> tuple[float, dict]:
    """Query primary external reviewer (Gemini/Groq). Returns (score, {provider: score})."""
    from external_reviewer import review_output_sync, ReviewType
    result = review_output_sync(text, SMT_COUNCIL_TASK, ReviewType.STRUCTURAL_MAPPING)
    if result.provider == "none" or result.score is None:
        # Two ways to have no opinion: nobody answered (the "none" sentinel),
        # or somebody answered with prose carrying no rating (score is None).
        # Both must skip the leg rather than contribute a number, because any
        # stand-in near 50 sits on the council's own veto line and would be
        # counted as a real vote. Checking both keeps the leg honest even if a
        # caller hands back one signal without the other.
        raise ProviderUnavailable(
            f"external reviewer supplied no score (provider={result.provider})")
    score = result.score * 100
    return score, {result.provider: score}


def _query_moe_council(text: str) -> tuple[float, dict]:
    """Query MoE external scoring council. Returns (score, {provider: score})."""
    from moe.external_scorer import external_score
    verdict = external_score(
        text,
        SMT_COUNCIL_TASK,
        task_type="structural_mapping",
        dimension_weights=SMT_WEIGHT_TABLE,
    )
    if not verdict.num_providers:
        raise ProviderUnavailable("MoE council returned no provider scores")
    composite = float(verdict.composite_score)
    return composite, {"moe_council": composite}


def _leg_code_objects(leg) -> tuple:
    """Every code object that counts as "inside the leg itself".

    A decorated leg is two functions wearing one name. ``functools.wraps``
    copies ``__name__`` and ``__doc__`` but NEVER ``__code__``, so a leg
    wrapped for retries or timing raises from the inner function's frame while
    ``leg.__code__`` still points at the wrapper. Comparing against only one of
    them misses the other, and the miss is silent.

    ``inspect.unwrap`` follows the ``__wrapped__`` chain that ``wraps`` leaves
    behind, so both ends are available to compare.
    """
    codes = [getattr(leg, "__code__", None)]
    try:
        codes.append(getattr(inspect.unwrap(leg), "__code__", None))
    except ValueError:          # a cyclic __wrapped__ chain; the wrapper alone will do
        pass
    return tuple(c for c in codes if c is not None)


def _is_call_shape_error(exc: BaseException, leg) -> bool:
    """True when ``exc`` is OUR bad call rather than a provider misbehaving.

    A wrong-arity call, or reading a field the return value does not have, is
    raised at the call site INSIDE the leg, so the traceback goes no deeper
    than the leg's own frame. Anything raised further in belongs to a provider
    and stays on the graceful-degradation path, so a TypeError thrown
    legitimately inside a provider is not mistaken for a bug in this module.

    Only the DEEPEST frame is compared, never the whole chain: the leg's frame
    appears in a provider-side traceback too (it is the caller), so matching
    anywhere would re-raise genuine outages and destroy the degradation the
    council depends on.
    """
    if not isinstance(exc, (TypeError, AttributeError)):
        return False
    deepest, tb = None, exc.__traceback__
    while tb is not None:
        deepest, tb = tb, tb.tb_next
    return deepest is not None and deepest.tb_frame.f_code in _leg_code_objects(leg)


def _poll_council(text: str, legs: tuple) -> tuple[list[float], dict]:
    """Ask every council leg for a score. Returns (scores, {provider: score}).

    A leg that declines is skipped so the run degrades rather than fails. A leg
    we called wrongly is re-raised: that is our bug, and swallowing it is what
    let half this council go unrun since it shipped (issue #4411).
    """
    leg_scores: list[float] = []
    provider_scores: dict = {}
    for leg in legs:
        try:
            score, scores = leg(text)
        except Exception as e:
            if _is_call_shape_error(e, leg):
                logger.error("[external_smt] Call-shape bug calling %s: %s",
                             getattr(leg, "__name__", repr(leg)), e)
                raise
            logger.warning("[external_smt] Provider failed: %s", e)
            continue
        provider_scores.update(scores)
        leg_scores.append(score)
    return leg_scores, provider_scores


def _build_verdict(mech_score: float, leg_scores: list, provider_scores: dict,
                   attempted: int) -> ExternalSmtVerdict:
    """Turn the council's answers into a verdict that states its own quorum."""
    n_providers = len(leg_scores)
    if n_providers == 0:
        logger.warning("[external_smt] All providers unavailable, mechanical-only fallback")
        return ExternalSmtVerdict(
            mechanical_score=mech_score, combined_score=mech_score,
            providers_attempted=attempted, decision="allow", externally_validated=False,
            reason="External providers unavailable, mechanical-only fallback")

    council_score = sum(leg_scores) / n_providers
    combined, decision = _combine_scores(mech_score, council_score)
    quorum_reached = n_providers >= COUNCIL_QUORUM
    reason = f"Mechanical {mech_score:.0f} + Council {council_score:.0f} = {combined:.0f}"
    if not quorum_reached:
        reason += f" (DEGRADED: {n_providers}/{attempted} council legs answered)"

    return ExternalSmtVerdict(
        mechanical_score=mech_score, council_score=council_score,
        council_providers=n_providers, providers_attempted=attempted,
        council_provider_scores=provider_scores,
        combined_score=combined, decision=decision,
        externally_validated=quorum_reached, reason=reason)


def score_mapping_external(proposal, mode: str = "standard") -> ExternalSmtVerdict:
    """Score a structural mapping via external multi-LLM council.

    Mechanical gate runs first (deterministic). If it passes, external
    council provides the numeric score. Graceful degradation on failure --
    but the verdict says so: ``externally_validated`` is True only when every
    leg the council is designed to consult actually answered, and
    ``providers_attempted`` records how many were asked.
    """
    from gentner_enforcer import enforce

    mechanical_result = enforce(proposal)
    mech_score = mechanical_result.score.total_score

    if mechanical_result.blocked:
        return ExternalSmtVerdict(
            mechanical_score=mech_score, decision="deny", reason=mechanical_result.reason)

    legs = (_query_external_reviewer, _query_moe_council)
    leg_scores, provider_scores = _poll_council(serialise_proposal(proposal), legs)
    return _build_verdict(mech_score, leg_scores, provider_scores, len(legs))
