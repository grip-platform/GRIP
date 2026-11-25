"""
converge_dna_fitness.py — Fitness update algorithms and review functions.

Handles all fitness computation: mechanical updates, ablation scoring,
time decay, KONO attestation, and LLM attribution review.

Depends on: converge_dna_core (data structures only).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.converge_dna_core import (
    CLAUDE_DIR,
    DECAY_RATE,
    DEAD_THRESHOLD,
    KONO_ATTESTATION_BONUS,
    KONO_PERMANENT_FLOOR,
    NEUTRAL_FITNESS,
    NURSERY_GENERATIONS,
    PROTECTED_FLOOR,
    REVIEW_IMPROVEMENT_THRESHOLD,
    REVIEW_REGRESSION_THRESHOLD,
    REVIEW_VERDICTS_PATH,
    SURVIVAL_FLOOR,
    ConvergeOutcome,
    EvaluationPolicy,
    Gene,
    Genome,
    ReviewVerdict,
)

logger = logging.getLogger(__name__)


# =========================================================================
# Fitness Update Algorithm
# =========================================================================

def update_gene_fitness(
    gene: Gene,
    outcome: ConvergeOutcome,
    policy: Optional[EvaluationPolicy] = None,
) -> float:
    """Update a single gene's fitness based on a converge outcome.

    The update is SMALL and INCREMENTAL. No single run can dramatically
    change fitness. Uses ablation scoring to distinguish causation from
    correlation. No LLM anywhere in this computation.

    Returns the new fitness value.
    """
    was_invoked = gene.name in outcome.invoked_genes
    was_expressed = gene.name in outcome.expressed_genes

    if not was_expressed:
        return gene.fitness

    p = policy or EvaluationPolicy()

    # Record ablation data
    gene.ablation.record(was_invoked, outcome.satisfied)

    # Compute ablation multiplier
    lift = gene.ablation.ablation_lift
    if gene.ablation.invoked_total >= 3 and gene.ablation.not_invoked_total >= 3:
        ablation_multiplier = max(0.0, min(2.0, 1.0 + lift * 2.0))
    else:
        ablation_multiplier = 1.0

    delta = 0.0

    if was_invoked and outcome.satisfied:
        raw_delta = p.get("invoked_success")
        delta = raw_delta * ablation_multiplier
        if outcome.depth_reached <= 1:
            delta += p.get("fast_convergence_bonus") * ablation_multiplier
    elif was_invoked and not outcome.satisfied:
        delta = p.get("invoked_failure")
    elif not was_invoked and outcome.satisfied:
        delta = p.get("expressed_success")
    elif not was_invoked and not outcome.satisfied:
        delta = p.get("expressed_failure")

    # Improvement bonus
    if was_invoked and outcome.improvement > p.get("high_improvement_threshold"):
        delta += p.get("high_improvement_bonus") * ablation_multiplier
    elif was_invoked and outcome.improvement < p.get("negative_improvement_threshold"):
        delta += p.get("negative_improvement_penalty")

    gene._base_fitness = max(0.0, min(1.0, gene._base_fitness + delta))

    if gene.protected:
        gene._base_fitness = max(PROTECTED_FLOOR, gene._base_fitness)
    if gene.kono_permanent:
        gene._base_fitness = max(KONO_PERMANENT_FLOOR, gene._base_fitness)

    gene.contribution_record.append((outcome.run_id, outcome.satisfied))
    if len(gene.contribution_record) > 50:
        gene.contribution_record = gene.contribution_record[-50:]

    if was_invoked:
        gene.invocation_count += 1
        gene.last_invoked = outcome.timestamp

    return gene.fitness


def update_gene_domain_fitness(
    gene: Gene,
    outcome: ConvergeOutcome,
) -> None:
    """Update gene's domain fitness based on CUBE scores from the outcome."""
    if not outcome.cube_scores or gene.name not in outcome.invoked_genes:
        return

    for domain, score in outcome.cube_scores.items():
        current = gene.domain_fitness.domain_scores.get(domain, NEUTRAL_FITNESS)
        delta = (score - current) * 0.1
        new_score = max(0.0, min(1.0, current + delta))
        gene.domain_fitness.domain_scores[domain] = round(new_score, 4)


def _decay_days_elapsed(gene: Gene, now: datetime) -> float:
    """Days since the gene's most recent activity OR prior decay.

    Reference is max(last_invoked, last_decayed) — both UTC isoformat, so the
    lexical max is chronological. Using last_invoked alone (absolute idle time,
    never reset by decay) made D idle days compound as 0.95^(1+2+...+D) across
    daily sessions instead of the documented 0.95^D. Fail-closed: with neither
    timestamp (or an unparseable one) it returns the 30-day default, so the
    change can only ever REDUCE over-decay.
    """
    ref = max(gene.last_invoked or "", gene.last_decayed or "")
    if not ref:
        return 30.0
    try:
        last = datetime.fromisoformat(ref)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (now - last).total_seconds() / 86400
    except (ValueError, TypeError):
        return 30.0


def apply_time_decay(genome: Genome) -> None:
    """Apply KONO-inspired time decay to all genes.

    Formula: fitness *= 0.95 ^ days
    Nursery hard cutoff: genes born within NURSERY_GENERATIONS of the current
    generation are fully immune to time-decay (nursery_factor = 0.0 → skip).
    This prevents newly-added genes from being killed before they have a chance
    to accumulate invocations.
    Daily dedup: only applies once per calendar day to prevent multi-session amplification.
    """
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # Daily dedup: prevent repeated decay from multiple session starts
    if genome.last_decay_at == today:
        logger.debug("[decay] Already applied today (%s), skipping", today)
        return

    for gene in genome.genes.values():
        if gene.protected:
            continue

        age_in_generations = genome.generation - gene.generation_born

        # Nursery hard cutoff: young genes are fully immune to time-decay.
        # Genes born within NURSERY_GENERATIONS of the current generation skip
        # decay entirely, giving them time to accumulate invocations before
        # being evaluated on fitness.
        if age_in_generations < NURSERY_GENERATIONS:
            continue

        days = _decay_days_elapsed(gene, now)
        decayed = gene._base_fitness * (DECAY_RATE ** days)
        gene._base_fitness = max(gene.fitness_floor, decayed)
        gene.last_decayed = now.isoformat()

    genome.last_decay_at = today


def apply_kono_attestation(gene: Gene, attestation_count: int) -> None:
    """Apply KONO attestation signals to gene fitness.

    3+ attestations = permanent memory = fitness floor of 0.6.
    """
    gene.kono_attestations = attestation_count
    bonus = min(attestation_count, 3) * KONO_ATTESTATION_BONUS
    gene._base_fitness = min(1.0, gene._base_fitness + bonus)

    if attestation_count >= 3:
        gene.kono_permanent = True
        gene._base_fitness = max(KONO_PERMANENT_FLOOR, gene._base_fitness)


# =========================================================================
# LLM Attribution Review
# =========================================================================

def should_review(outcome: ConvergeOutcome) -> bool:
    """Determine if this outcome warrants an LLM attribution review.

    Reviews are conditional (~30% of runs) to control token cost.
    """
    if outcome.improvement > REVIEW_IMPROVEMENT_THRESHOLD:
        return True
    if outcome.improvement < REVIEW_REGRESSION_THRESHOLD:
        return True
    if not outcome.satisfied and outcome.depth_reached >= 3:
        return True
    return False


def build_review_prompt(outcome: ConvergeOutcome, invoked_genes: list[str]) -> str:
    """Build the structured prompt for LLM attribution review.

    Asks for CAUSAL ATTRIBUTION, not quality scoring.
    """
    return f"""You are evaluating a converge run. DO NOT score quality.
Only attribute WHICH skills caused the outcome.

Task type: converge run {outcome.run_id}
Score trajectory: {outcome.score_before:.0f} -> {outcome.score_after:.0f} (delta: {outcome.improvement:+.0f})
Depth reached: {outcome.depth_reached}
Satisfied: {outcome.satisfied}
Invoked genes: {', '.join(invoked_genes)}

Return JSON only:
{{
  "attributions": {{"gene_name": weight}},
  "negative": {{"gene_name": weight}},
  "genesis": [{{"name": "...", "source": "...", "behavior": "..."}}],
  "reasoning": "one sentence"
}}

Rules:
- attributions weights in [0.0, 1.0], must sum to <= 1.0
- negative weights in [-1.0, 0.0] for genes that hurt the outcome
- genesis: propose micro-skills (3-10 line behaviors) not captured by existing genes
- genesis may be empty []
- reasoning: one sentence explaining the causal chain"""


def parse_review_response(response_json: str, run_id: str = "") -> Optional[ReviewVerdict]:
    """Parse agent's JSON response into a ReviewVerdict.

    Tolerant of malformed input — returns None on parse failure.
    """
    try:
        data = json.loads(response_json)
    except (json.JSONDecodeError, TypeError):
        return None

    verdict = ReviewVerdict(
        attributions=data.get("attributions", {}),
        negative_attributions=data.get("negative", {}),
        proposed_skills=data.get("genesis", []),
        reasoning=data.get("reasoning", ""),
        run_id=run_id,
    )
    verdict.normalize()
    return verdict


def save_verdict(verdict: ReviewVerdict) -> None:
    """Persist review verdict to JSONL for analysis."""
    REVIEW_VERDICTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "run_id": verdict.run_id,
        "attributions": verdict.attributions,
        "negative_attributions": verdict.negative_attributions,
        "proposed_skills": verdict.proposed_skills,
        "reasoning": verdict.reasoning,
        "timestamp": verdict.timestamp,
    }
    with open(REVIEW_VERDICTS_PATH, "a") as f:
        f.write(json.dumps(data, default=str) + "\n")


def apply_review_verdict(
    genome: Genome,
    outcome: ConvergeOutcome,
    verdict: ReviewVerdict,
    policy: Optional[EvaluationPolicy] = None,
) -> Genome:
    """Apply LLM attribution weights on top of mechanical fitness.

    Called AFTER evolve_generation() has already applied mechanical updates.
    The verdict scales the delta that was already applied.
    """
    p = policy or genome.eval_policy

    for gene in genome.genes.values():
        if gene.name not in outcome.invoked_genes:
            continue

        weight = verdict.get_weight(gene.name)

        if weight < 0:
            base_delta = p.get("invoked_success") if outcome.satisfied else 0.0
            reversal = base_delta
            penalty = abs(weight) * abs(p.get("invoked_failure"))
            gene._base_fitness = max(gene.fitness_floor, gene._base_fitness - reversal - penalty)
        elif weight < 1.0:
            base_delta = p.get("invoked_success") if outcome.satisfied else p.get("invoked_failure")
            excess = base_delta * (1.0 - weight)
            gene._base_fitness = max(gene.fitness_floor, gene._base_fitness - excess)

        if gene.protected:
            gene._base_fitness = max(PROTECTED_FLOOR, gene._base_fitness)
        if gene.kono_permanent:
            gene._base_fitness = max(KONO_PERMANENT_FLOOR, gene._base_fitness)

    genome.id = genome._compute_id()
    return genome
