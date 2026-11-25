#!/usr/bin/env python3
"""
converge_dna.py — Evolutionary DNA layer for GRIP's converge() kernel.

Integration hub that re-exports from submodules for backward compatibility.
Split into 4 modules for SRP compliance:

- converge_dna_core.py: Data structures, constants, InvocationRegistry
- converge_dna_fitness.py: Fitness updates, time decay, review functions
- converge_dna_operators.py: Genetic operators (mutation, crossover, selection)
- converge_dna.py (this file): Integration, persistence, bootstrap, CLI

Usage:
    from lib.converge_dna import (
        Gene, DomainFitness, Genome, ConvergeOutcome,
        bootstrap_genome, load_genome, save_genome,
        converge_with_genome, evolve_generation,
        point_mutation, insertion, deletion, crossover,
        register_gene_invocation, clear_invocation_registry,
        get_invoked_genes, validate_genome,
    )

See docs/EVOLUTIONARY-DNA-DESIGN.md for full architecture.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.memory_lock import locked_write

# =========================================================================
# Re-exports from submodules (backward compatibility)
# =========================================================================

from lib.converge_dna_core import (  # noqa: E402, F401
    CLAUDE_DIR,
    CONTENT_COMPRESS_THRESHOLD,
    CUBE_DOMAINS,
    DEAD_THRESHOLD,
    DEFAULT_EVAL_POLICY,
    DECAY_RATE,
    GENOME_HISTORY_PATH,
    GENOME_OUTCOMES_PATH,
    GENOME_PATH,
    KONO_ATTESTATION_BONUS,
    KONO_PERMANENT_FLOOR,
    NEUTRAL_FITNESS,
    NURSERY_GENERATIONS,
    PROTECTED_FLOOR,
    REVIEW_IMPROVEMENT_THRESHOLD,
    REVIEW_REGRESSION_THRESHOLD,
    REVIEW_VERDICTS_PATH,
    AblationScore,
    ConvergeOutcome,
    DomainFitness,
    EvaluationPolicy,
    Gene,
    Genome,
    InvocationRegistry,
    ReviewVerdict,
    clear_invocation_registry,
    compress_content,
    decompress_content,
    get_invoked_genes,
    register_gene_invocation,
)

from lib.converge_dna_fitness import (  # noqa: E402, F401
    apply_kono_attestation,
    apply_review_verdict,
    apply_time_decay,
    build_review_prompt,
    parse_review_response,
    save_verdict,
    should_review,
    update_gene_domain_fitness,
    update_gene_fitness,
)

from lib.converge_dna_operators import (  # noqa: E402, F401
    crossover,
    deletion,
    insertion,
    point_mutation,
    reproduce,
    select,
    split_gene,
)


# =========================================================================
# Micro-Skill Genesis
# =========================================================================

def propose_micro_skill(
    proposal: dict,
    genome: Genome,
) -> tuple[Genome, Optional[Path]]:
    """Create a micro-skill from a review genesis proposal.

    Writes a minimal SKILL.md and inserts a Gene into the genome.
    """
    name = proposal.get("name", "").strip()
    source = proposal.get("source", "unknown")
    behavior = proposal.get("behavior", "")

    if not name or not behavior:
        return genome, None

    safe_name = name.lower().replace(" ", "-").replace("_", "-")
    safe_name = "".join(c for c in safe_name if c.isalnum() or c == "-")

    if safe_name in genome.genes:
        return genome, None

    skill_dir = CLAUDE_DIR / "skills" / safe_name
    skill_path = skill_dir / "SKILL.md"

    if skill_path.exists():
        return genome, None

    # Extract friction_type and evidence_keywords from proposal if available
    friction_type = proposal.get("friction_type", "")
    evidence_keywords = proposal.get("evidence_keywords", [])

    # Build optional frontmatter fields for evo genesis loop
    extra_fm = ""
    if friction_type:
        extra_fm += f"friction_type: {friction_type}\n"
    if evidence_keywords:
        extra_fm += "evidence_keywords:\n"
        for kw in evidence_keywords:
            extra_fm += f"  - {kw}\n"

    # `description` is model-generated prose from a genesis proposal, so it is
    # emitted as a JSON string: YAML 1.2 is a superset of JSON, so json.dumps
    # yields a valid double-quoted scalar with colons and quotes escaped. A
    # colon-space in the behavior text otherwise makes YAML read a nested mapping
    # and the emitted SKILL.md has unparseable frontmatter. Same defect fixed in
    # lib/auto_memory_writer.py (GRIP#6096) and lib/remember.py (GRIP#6100).
    #
    # `safe_name` stays bare: it is slugified to alnum + hyphen above, so it
    # cannot contain a colon. Quoting it would churn every existing skill file.
    skill_content = f"""---
name: {safe_name}
description: {json.dumps(behavior)}
status: Active
version: 0.1.0
lineage: genesis:{source}
{extra_fm}---

# {safe_name}

{behavior}
"""

    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(skill_content, encoding="utf-8")
    except OSError:
        return genome, None

    gene = Gene(
        name=safe_name,
        component_type="skill",
        lineage=f"genesis:{source}",
        generation_born=genome.generation,
    )

    try:
        genome = insertion(genome, gene)
    except ValueError:
        return genome, None

    logger.info("[genesis] Created micro-skill '%s' from '%s': %s", safe_name, source, behavior)
    return genome, skill_path


def apply_genesis_proposals(
    genome: Genome,
    verdict: ReviewVerdict,
) -> tuple[Genome, list[Path]]:
    """Process all genesis proposals from a review verdict."""
    created = []
    for proposal in verdict.proposed_skills:
        genome, path = propose_micro_skill(proposal, genome)
        if path:
            created.append(path)
    return genome, created


# =========================================================================
# Micro-Skill Discovery (Phase 3)
# =========================================================================

def discover_split_candidates(
    genome: Genome,
    skills_dir: Optional[Path] = None,
    min_lines: int = 300,
    min_sections: int = 4,
) -> list[dict]:
    """Scan SKILL.md files for monoliths that should be split into micro-skills.

    A skill is a split candidate if:
    - Its SKILL.md is over min_lines lines, AND
    - It has >3 ## Phase sections, >5 ## Step sections, or >= min_sections
      top-level ## sections.

    Returns a list of dicts with keys: gene_name, path, line_count,
    section_count, sections (list of heading strings).
    """
    import re

    base = skills_dir or (CLAUDE_DIR / "skills")
    if not base.exists():
        return []

    candidates = []
    for skill_dir in sorted(base.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue

        try:
            # Only lines and headings are counted here, so a bad byte
            # becomes U+FFFD rather than ending the whole scan.
            content = skill_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        lines = content.splitlines()
        line_count = len(lines)
        if line_count < min_lines:
            continue

        # Extract top-level ## headings (not ### or deeper)
        h2_headings = re.findall(r'^## (.+)$', content, re.MULTILINE)
        phase_count = sum(1 for h in h2_headings if h.strip().lower().startswith('phase'))
        step_count = sum(1 for h in h2_headings if h.strip().lower().startswith('step'))

        is_monolith = (
            phase_count > 3
            or step_count > 5
            or len(h2_headings) >= min_sections
        )

        if is_monolith:
            gene_name = skill_dir.name
            candidates.append({
                "gene_name": gene_name,
                "path": skill_file,
                "line_count": line_count,
                "section_count": len(h2_headings),
                "sections": h2_headings,
            })

    return candidates


def _autosplit_candidates(genome: Genome) -> int:
    """Wire micro-skill splitting into the genome evolution loop.

    Scans for monolith skills and creates child genes for each qualifying
    ## section. Parent gene is retained for lineage. Dead genes are skipped.

    Returns the number of new genes added to the genome.
    """
    candidates = discover_split_candidates(genome)
    added = 0
    for c in candidates:
        gene = genome.genes.get(c["gene_name"])
        if gene is None or gene.fitness <= DEAD_THRESHOLD:
            continue
        try:
            # Strict: split_gene stores each section's text in the genome, so
            # a file that is not valid UTF-8 is skipped, never saved as guesses.
            content = c["path"].read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for child in split_gene(gene, content):
            if child.name not in genome.genes:
                genome.genes[child.name] = child
                added += 1
                logger.info(
                    "[autosplit] %s -> %s (fitness=%.3f)",
                    gene.name, child.name, child.fitness,
                )
    return added


# =========================================================================
# Marketplace Adapters (Genome Sharing)
# =========================================================================

def genome_to_marketplace(
    genome: Genome,
    org_slug: str,
    min_fitness: float = 0.6,
) -> list:
    """Convert living genes with fitness >= threshold to ConvergenceStrategy objects.

    Returns a list of ConvergenceStrategy instances compatible with
    converge_marketplace.export_package(). Only exports name, fitness,
    and usage stats — no session IDs, commit SHAs, or MEMORY content.
    """
    from lib.converge_inherit import ConvergenceStrategy

    strategies = []
    for gene in genome.genes.values():
        if gene.fitness < min_fitness:
            continue
        succeeded = sum(1 for _, s in gene.contribution_record if s)
        strategies.append(ConvergenceStrategy(
            domain=gene.component_type,
            strategy_name=gene.name,
            description=f"Gene from {org_slug} genome (gen {genome.generation})",
            source_project=org_slug,
            score_before=0.5,
            score_after=gene.fitness,
            times_applied=max(gene.invocation_count, 1),
            times_succeeded=max(succeeded, 1),
            tags=list(gene.expression_contexts),
            content_hash=gene.content_hash,
        ))
    return strategies


def marketplace_to_genes(
    strategies: list[dict],
    target_genome: Genome,
    trust_level: float = 0.8,
) -> int:
    """Import marketplace strategies as genes into a target genome.

    Trust-level discounts imported fitness. Protected genes cannot be
    overwritten below their floor. Returns count of genes added/updated.
    """
    count = 0
    for s in strategies:
        name = s.get("strategy_name", "")
        if not name:
            continue
        imported_fitness = s.get("confidence", 0.5) * trust_level

        if name in target_genome.genes:
            existing = target_genome.genes[name]
            if existing.protected and imported_fitness < existing.fitness_floor:
                continue
            if imported_fitness > existing.fitness:
                existing.fitness = imported_fitness
                count += 1
        else:
            target_genome.genes[name] = Gene(
                name=name,
                component_type=s.get("domain", "skill"),
                _base_fitness=imported_fitness,
                generation_born=target_genome.generation,
                content_hash=s.get("content_hash", ""),
            )
            count += 1
    return count


# =========================================================================
# Integration: converge_with_genome()
# =========================================================================

def converge_with_genome(
    state: Any,
    criterion: Callable[[Any], bool],
    improve: Callable[[Any], Any],
    genome: Genome,
    max_depth: int = 3,
    _scorer: Optional[Callable[[Any], float]] = None,
    label: str = "",
    context: str = "",
) -> tuple[Any, ConvergeOutcome]:
    """Genome-aware wrapper around converge().

    Before: Snapshots genome state and expressed genes.
    During: Runs converge() (unmodified kernel).
    After: Records ConvergeOutcome linking genes to results.
    """
    from lib.converge import converge

    # 1. Snapshot: which genes are expressed in this context
    expressed = [g.name for g in genome.express(context)]

    # 2. Record initial score
    score_before = _scorer(state) if _scorer else 0.0

    # 3. Set up invocation tracking via explicit registry
    clear_invocation_registry()

    # 4. Run converge (unmodified kernel)
    # NOTE: improve() is responsible for calling register_gene_invocation()
    # for each gene it uses. Fallback: _extract_invoked_genes() heuristic.
    start_time = time.monotonic()
    result = converge(
        state=state,
        criterion=criterion,
        improve=improve,
        max_depth=max_depth,
        _scorer=_scorer,
        label=label,
    )
    elapsed = time.monotonic() - start_time

    # 5. Get invoked genes from deterministic InvocationRegistry
    invoked = get_invoked_genes()

    # 6. Fetch CUBE scores for this outcome (populates domain fitness)
    cube_scores = {}
    try:
        from lib.subsystem_bridge import cube_scores_for_outcome
        cube_scores = cube_scores_for_outcome(".")
    except Exception:
        pass

    # 7. Build outcome
    score_after = _scorer(result.best_state) if _scorer else 0.0
    outcome = ConvergeOutcome(
        run_id=label or str(uuid.uuid4())[:8],
        genome_id=genome.id,
        generation=genome.generation,
        expressed_genes=expressed,
        invoked_genes=invoked,
        satisfied=result.satisfied,
        score_before=score_before,
        score_after=score_after,
        depth_reached=result.depth_reached,
        elapsed_sec=elapsed,
        context=context,
        cube_scores=cube_scores,
    )

    return result, outcome



# =========================================================================
# Evolutionary Loop
# =========================================================================

def _detect_genome_op_mode() -> str:
    """Detect genome operation mode from token budget."""
    try:
        from lib.token_budget import TokenBudget
        return TokenBudget.detect().genome_mode()
    except (ImportError, Exception) as e:
        logger.warning("Budget detection failed, defaulting to read-only: %s", e)
        return "read-only"


def _evolve_read_only(genome: Genome, outcome: ConvergeOutcome) -> Genome:
    """Minimal evolution for scarce budget mode — fitness updates only."""
    new_genome = deepcopy(genome)
    new_genome.generation += 1
    new_genome.parent_id = genome.id
    new_genome.total_converge_runs += 1
    if outcome.satisfied:
        new_genome.successful_runs += 1
    for gene in new_genome.genes.values():
        update_gene_fitness(gene, outcome, policy=new_genome.eval_policy)
        if outcome.cube_scores:
            update_gene_domain_fitness(gene, outcome)
    new_genome.fitness_history.append(round(new_genome.avg_fitness, 4))
    if len(new_genome.fitness_history) > 20:
        new_genome.fitness_history = new_genome.fitness_history[-20:]
    new_genome.id = new_genome._compute_id()
    logger.info("[evolve] read-only update gen %d -> %d (budget: scarce)",
                genome.generation, new_genome.generation)
    return new_genome


def _sync_kono_bridges(
    new_genome: Genome, old_genome: Genome, outcome: ConvergeOutcome
) -> None:
    """Sync KONO attestations to/from genome (bidirectional bridge)."""
    try:
        from lib.subsystem_bridge import sync_kono_attestations_to_genome
        synced = sync_kono_attestations_to_genome(new_genome)
        if synced:
            logger.debug("[evolve] synced %d KONO attestations to genes", synced)
    except (ImportError, Exception):
        pass
    try:
        from lib.subsystem_bridge import store_gene_to_kono
        for gene in new_genome.genes.values():
            old_gene = old_genome.genes.get(gene.name)
            if old_gene and abs(gene.fitness - old_gene.fitness) > 0.05:
                store_gene_to_kono(gene, outcome)
    except (ImportError, Exception):
        pass


def _apply_verdict_and_genesis(
    new_genome: Genome, outcome: ConvergeOutcome, verdict: ReviewVerdict
) -> Genome:
    """Apply LLM review verdict and process genesis proposals."""
    new_genome = apply_review_verdict(new_genome, outcome, verdict, new_genome.eval_policy)
    save_verdict(verdict)
    new_genome, created_paths = apply_genesis_proposals(new_genome, verdict)
    if created_paths:
        logger.info(
            "[genesis] Created %d micro-skills: %s",
            len(created_paths),
            [p.parent.name for p in created_paths],
        )
    return new_genome


def _post_evolve_housekeeping(new_genome: Genome) -> None:
    """Record fitness trajectory, adapt mutation rate, auto-split, and promote."""
    new_genome.fitness_history.append(round(new_genome.avg_fitness, 4))
    if len(new_genome.fitness_history) > 20:
        new_genome.fitness_history = new_genome.fitness_history[-20:]
    new_genome.adaptive_mutation_rate()
    if new_genome.generation % 10 == 0:
        _autosplit_candidates(new_genome)
    try:
        from lib.subsystem_bridge import promote_gene_to_shared
        for gene in new_genome.genes.values():
            if gene.fitness >= 0.7 and gene.invocation_count >= 3:
                promote_gene_to_shared(gene.name)
    except (ImportError, Exception):
        pass


def evolve_generation(
    genome: Genome,
    outcome: ConvergeOutcome,
    verdict: Optional[ReviewVerdict] = None,
) -> Genome:
    """Process one evolutionary generation.

    Called after each converge() run:
    1. Update fitness for all genes based on the outcome.
    2. Update domain fitness if CUBE scores available.
    3. Apply LLM attribution if a ReviewVerdict is provided.
    4. Process genesis proposals from the verdict.
    5. Record fitness trajectory for adaptive mutation rate.
    6. Adapt the mutation rate based on fitness plateau detection.
    """
    genome_op_mode = _detect_genome_op_mode()

    if genome_op_mode == "lookup":
        logger.info("[evolve] genome frozen (budget: oracle/lookup)")
        return genome

    if genome_op_mode == "read-only":
        return _evolve_read_only(genome, outcome)

    new_genome = deepcopy(genome)
    new_genome.generation += 1
    new_genome.parent_id = genome.id
    new_genome.total_converge_runs += 1
    if outcome.satisfied:
        new_genome.successful_runs += 1

    _sync_kono_bridges(new_genome, genome, outcome)

    # Mechanical fitness update
    for gene in new_genome.genes.values():
        update_gene_fitness(gene, outcome, policy=new_genome.eval_policy)
        if outcome.cube_scores:
            update_gene_domain_fitness(gene, outcome)

    # Tag expression_contexts with wave phase (W2c)
    if outcome.context and outcome.context.startswith("wave:"):
        for gene_name in outcome.invoked_genes:
            gene = new_genome.genes.get(gene_name)
            if gene and outcome.context not in gene.expression_contexts:
                gene.expression_contexts.append(outcome.context)

    if verdict:
        new_genome = _apply_verdict_and_genesis(new_genome, outcome, verdict)

    _post_evolve_housekeeping(new_genome)

    new_genome.id = new_genome._compute_id()

    reviewed = " [reviewed]" if verdict else ""
    logger.info(
        "[evolve] gen %d -> %d | %d genes | avg_fitness=%.3f | "
        "success_rate=%.1f%% | mutation_rate=%.3f%s",
        genome.generation,
        new_genome.generation,
        len(new_genome.genes),
        new_genome.avg_fitness,
        new_genome.success_rate() * 100,
        new_genome.mutation_rate,
        reviewed,
    )

    return new_genome


def evolve(
    population: list[Genome],
    task_fn: Callable[[Genome], ConvergeOutcome],
    generations: int = 10,
    population_size: int = 5,
    mutation_rate: float = 0.1,
    crossover_rate: float = 0.2,
) -> Genome:
    """Run a population-level evolutionary loop."""
    for gen in range(generations):
        outcomes = [(genome, task_fn(genome)) for genome in population]
        population = [evolve_generation(g, o) for g, o in outcomes]
        population = select(population, population_size)

        if len(population) >= 2:
            best_rate = population[0].mutation_rate or mutation_rate
            offspring = reproduce(population, best_rate, crossover_rate)
            population.extend(offspring)

        population = select(population, population_size)

        best = population[0] if population else None
        if best:
            logger.info(
                "[evolve] generation %d | best avg_fitness=%.3f | "
                "genes=%d | mutation_rate=%.3f",
                gen, best.avg_fitness, len(best.genes),
                best.mutation_rate,
            )

    return population[0] if population else Genome()


# =========================================================================
# Persistence
# =========================================================================

def _omit_gene_default_fields(d: dict) -> dict:
    """Drop fields matching Gene dataclass defaults to shrink genome.json.

    Lossless: _gene_from_dict() refills any omitted field from the Gene
    dataclass defaults (W3a: default-omission compression).
    """
    _defaults = {
        "lineage": None,
        "mutations": 0,
        "expression_contexts": [],
        "contribution_record": [],
        "parameters": {},
        "kono_attestations": 0,
        "kono_permanent": False,
        "compressed_content": "",
        "protected": False,
        "invocation_count": 0,
        "last_invoked": "",
        "last_decayed": "",
        "generation_born": 0,
    }
    for key, default_val in _defaults.items():
        if d.get(key) == default_val:
            del d[key]
    # Omit empty domain_fitness
    if not d.get("domain_fitness"):
        d.pop("domain_fitness", None)
    # Omit zeroed ablation
    abl = d.get("ablation", {})
    if all(v == 0 for v in abl.values()):
        d.pop("ablation", None)
    return d


def _gene_to_dict(gene: Gene, omit_defaults: bool = True) -> dict:
    """Serialize a Gene to a JSON-safe dict.

    When omit_defaults=True, fields matching their default values are excluded
    to reduce genome.json size (W3a: default-omission compression).
    Deserialization in _gene_from_dict() fills defaults from Gene dataclass.
    """
    d = {
        "name": gene.name,
        "content_hash": gene.content_hash,
        "component_type": gene.component_type,
        "fitness": gene._base_fitness,
        "lineage": gene.lineage,
        "generation_born": gene.generation_born,
        "mutations": gene.mutations,
        "expression_contexts": gene.expression_contexts,
        "invocation_count": gene.invocation_count,
        "contribution_record": gene.contribution_record,
        "last_invoked": gene.last_invoked,
        "last_decayed": gene.last_decayed,
        "parameters": gene.parameters,
        "protected": gene.protected,
        "domain_fitness": gene.domain_fitness.domain_scores,
        "kono_attestations": gene.kono_attestations,
        "kono_permanent": gene.kono_permanent,
        "ablation": {
            "invoked_successes": gene.ablation.invoked_successes,
            "invoked_failures": gene.ablation.invoked_failures,
            "not_invoked_successes": gene.ablation.not_invoked_successes,
            "not_invoked_failures": gene.ablation.not_invoked_failures,
        },
        "compressed_content": gene.compressed_content,
    }

    if omit_defaults:
        d = _omit_gene_default_fields(d)

    return d


def _gene_to_trimmed_dict(gene: Gene) -> dict:
    """Lightweight gene snapshot for history — 5 fields only (~103 bytes).

    Used by save_genome() for generation-boundary snapshots.
    Omits heavy fields (compressed_content, ablation, domain_fitness)
    that inflate full _gene_to_dict() to ~2.7KB per gene.
    """
    return {
        "name": gene.name,
        "fitness": round(gene.fitness, 4),
        "invocation_count": gene.invocation_count,
        "status": gene.tier,
        "generation_born": gene.generation_born,
    }


def _gene_from_dict(d: dict) -> Gene:
    """Deserialize a Gene from a dict."""
    gene = Gene(name=d["name"])
    gene.content_hash = d.get("content_hash", "")
    gene.component_type = d.get("component_type", "skill")
    raw_fitness = d.get("fitness")
    gene._base_fitness = raw_fitness if raw_fitness is not None else NEUTRAL_FITNESS
    gene.lineage = d.get("lineage")
    gene.generation_born = d.get("generation_born", 0)
    gene.mutations = d.get("mutations", 0)
    gene.expression_contexts = d.get("expression_contexts", [])
    gene.invocation_count = d.get("invocation_count", 0)
    gene.contribution_record = [
        tuple(cr) if isinstance(cr, list) else cr
        for cr in d.get("contribution_record", [])
    ]
    gene.last_invoked = d.get("last_invoked", "")
    gene.last_decayed = d.get("last_decayed", "")
    gene.parameters = d.get("parameters", {})
    gene.protected = d.get("protected", False)
    raw_df = d.get("domain_fitness", {})
    # Handle both formats: flat {"dim": score} and nested {"domain_scores": {...}}
    if isinstance(raw_df, dict) and "domain_scores" in raw_df:
        ds = raw_df["domain_scores"]
    else:
        ds = raw_df
    # Ensure all values are numeric (guard against further nesting)
    ds = {k: v for k, v in ds.items() if isinstance(v, (int, float))}
    gene.domain_fitness = DomainFitness(domain_scores=ds)
    gene.kono_attestations = d.get("kono_attestations", 0)
    gene.kono_permanent = d.get("kono_permanent", False)
    ablation_data = d.get("ablation", {})
    gene.ablation = AblationScore(
        invoked_successes=ablation_data.get("invoked_successes", 0),
        invoked_failures=ablation_data.get("invoked_failures", 0),
        not_invoked_successes=ablation_data.get("not_invoked_successes", 0),
        not_invoked_failures=ablation_data.get("not_invoked_failures", 0),
    )
    gene.compressed_content = d.get("compressed_content", "")
    return gene


def save_genome(genome: Genome, path: Optional[Path] = None, *, reason: str = "checkpoint") -> Path:
    """Serialize genome to JSON and append snapshot to history.

    Uses advisory file locking (fcntl) and atomic write (temp + rename)
    to prevent data loss from concurrent sessions.
    """
    target = path or GENOME_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "generation": genome.generation,
        "id": genome.id,
        "parent_id": genome.parent_id,
        "created_at": genome.created_at,
        "instance_id": genome.instance_id,
        "total_converge_runs": genome.total_converge_runs,
        "successful_runs": genome.successful_runs,
        "eval_policy": genome.eval_policy.params,
        "mutation_rate": genome.mutation_rate,
        "fitness_history": genome.fitness_history,
        "last_decay_at": genome.last_decay_at,
        "genes": {name: _gene_to_dict(gene) for name, gene in genome.genes.items()},
    }

    locked_write(target, json.dumps(data, indent=2, default=str))

    history_path = path.parent / "genome-history.jsonl" if path else GENOME_HISTORY_PATH
    history_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "snapshot_type": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "generation": genome.generation,
        "id": genome.id,
        "parent_id": genome.parent_id,
        "gene_count": len(genome.genes),
        "avg_fitness": round(genome.avg_fitness, 4),
        "living_count": len(genome.living_genes()),
        "dead_count": len(genome.dead_genes()),
        "success_rate": round(genome.success_rate(), 4),
    }
    if reason == "generation_boundary":
        snapshot["genes"] = {
            name: _gene_to_trimmed_dict(gene)
            for name, gene in genome.genes.items()
        }
    with open(history_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, default=str) + "\n")
    if reason == "generation_boundary":
        _maybe_tag_milestone(genome, history_path)

    return target


# =========================================================================
# Milestone Auto-Tagging
# =========================================================================

_GENERATION_MILESTONES = [10, 25, 50, 100, 250, 500]
_FITNESS_MILESTONES = [0.6, 0.7, 0.8, 0.9]
_GENE_COUNT_MILESTONES = [100, 150, 200, 250]


def _read_previous_snapshot(history_path: Path, current_gen: int) -> Optional[dict]:
    """Read the most recent snapshot before current_gen from history."""
    if not history_path.exists():
        return None
    prev = None
    for line in history_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("generation", 0) < current_gen:
            prev = entry
    return prev


def _detect_milestone(
    genome: Genome, previous: Optional[dict]
) -> Optional[str]:
    """Detect if current state crosses a milestone threshold.

    Returns tag string (e.g. 'genome/v100') or None.
    """
    gen = genome.generation
    for threshold in _GENERATION_MILESTONES:
        if gen == threshold:
            return f"genome/v{gen}"

    if previous is None:
        return None

    prev_fitness = previous.get("avg_fitness", 0.0)
    curr_fitness = genome.avg_fitness
    for threshold in _FITNESS_MILESTONES:
        if prev_fitness < threshold <= curr_fitness:
            return f"genome/fitness-{int(threshold * 100)}"

    prev_count = previous.get("gene_count", 0)
    curr_count = len(genome.genes)
    for threshold in _GENE_COUNT_MILESTONES:
        if prev_count < threshold <= curr_count:
            return f"genome/genes-{curr_count}"

    return None


def _create_git_tag(tag: str, message: str) -> Optional[str]:
    """Create an annotated git tag. Returns tag name or None on failure."""
    import subprocess
    try:
        subprocess.run(
            ["git", "tag", "-a", tag, "-m", message],
            capture_output=True,
            timeout=10,
            check=True,
            cwd=str(CLAUDE_DIR),
        )
        return tag
    except Exception:
        return None


def _maybe_tag_milestone(genome: Genome, history_path: Path) -> Optional[str]:
    """Check for milestone crossing and create git tag if detected."""
    previous = _read_previous_snapshot(history_path, genome.generation)
    tag = _detect_milestone(genome, previous)
    if tag is None:
        return None
    message = (
        f"Genome milestone: {tag} "
        f"(gen {genome.generation}, {len(genome.genes)} genes, "
        f"avg fitness {genome.avg_fitness:.4f})"
    )
    logger.info("Milestone detected: %s", tag)
    return _create_git_tag(tag, message)


def load_genome(path: Optional[Path] = None) -> Optional[Genome]:
    """Load genome from JSON. Returns None if no genome file exists."""
    target = path or GENOME_PATH
    if not target.exists():
        return None

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    genome = Genome(
        generation=data.get("generation", 0),
        id=data.get("id", ""),
        parent_id=data.get("parent_id", ""),
        created_at=data.get("created_at", ""),
        instance_id=data.get("instance_id", ""),
        total_converge_runs=data.get("total_converge_runs", 0),
        successful_runs=data.get("successful_runs", 0),
        mutation_rate=data.get("mutation_rate", 0.1),
        fitness_history=data.get("fitness_history", []),
        last_decay_at=data.get("last_decay_at", ""),
    )
    if "eval_policy" in data and isinstance(data["eval_policy"], dict):
        genome.eval_policy = EvaluationPolicy(params=data["eval_policy"])

    genes_data = data.get("genes", {})
    for name, gene_dict in genes_data.items():
        gene_dict["name"] = name
        genome.genes[name] = _gene_from_dict(gene_dict)

    return genome


def save_outcome(outcome: ConvergeOutcome) -> None:
    """Append outcome to genome-outcomes.jsonl."""
    GENOME_OUTCOMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "run_id": outcome.run_id,
        "genome_id": outcome.genome_id,
        "generation": outcome.generation,
        "expressed_genes": outcome.expressed_genes,
        "invoked_genes": outcome.invoked_genes,
        "satisfied": outcome.satisfied,
        "score_before": outcome.score_before,
        "score_after": outcome.score_after,
        "improvement": outcome.improvement,
        "depth_reached": outcome.depth_reached,
        "elapsed_sec": outcome.elapsed_sec,
        "timestamp": outcome.timestamp,
        "context": outcome.context,
        "cube_scores": outcome.cube_scores,
    }
    with open(GENOME_OUTCOMES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, default=str) + "\n")


# =========================================================================
# Bootstrap
# =========================================================================

def _compute_file_hash(path: Path) -> str:
    try:
        content = path.read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]
    except (OSError, IOError):
        return ""


def _scan_components() -> list[dict]:
    """Scan filesystem for skills, agents, rules, and lib files."""
    components = []

    skills_dir = CLAUDE_DIR / "skills"
    if skills_dir.exists():
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir():
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.exists():
                    skill_file = skill_dir / "SKILL.grip"
                if skill_file.exists():
                    components.append({
                        "name": skill_dir.name,
                        "type": "skill",
                        "path": skill_file,
                    })

    agents_dir = CLAUDE_DIR / "agents"
    if agents_dir.exists():
        for agent_file in agents_dir.glob("*.md"):
            components.append({
                "name": agent_file.stem,
                "type": "agent",
                "path": agent_file,
            })

    rules_dir = CLAUDE_DIR / "rules"
    if rules_dir.exists():
        for rule_file in rules_dir.glob("*.md"):
            if rule_file.name != "CLAUDE.md":
                components.append({
                    "name": rule_file.stem,
                    "type": "rule",
                    "path": rule_file,
                })

    modes_dir = CLAUDE_DIR / "modes" / "definitions"
    if modes_dir.exists():
        for mode_file in modes_dir.glob("*.yaml"):
            components.append({
                "name": mode_file.stem,
                "type": "mode",
                "path": mode_file,
            })

    return components


def _get_protected_set() -> set[str]:
    protected = {
        "converge", "context-gate", "asking-users",
        "concise-communication", "design-principles",
        "code-mode", "security-mode", "testing-mode",
    }
    try:
        from lib.evolutionary_refactor import PROTECTED
        for component_type_set in PROTECTED.values():
            protected.update(component_type_set)
    except (ImportError, AttributeError):
        pass
    return protected


def resurrect_dead_genes(genome: Genome) -> int:
    """Re-scan disk and resurrect dead genes that still have files on disk.

    Sets fitness to NEUTRAL_FITNESS (0.5) and resets last_invoked to now.
    Does NOT touch living genes -- only dead ones with existing files.
    """
    count = 0
    for gene in genome.genes.values():
        if gene.tier == "dead":
            skill_path = CLAUDE_DIR / "skills" / gene.name / "SKILL.md"
            agent_path = CLAUDE_DIR / "agents" / f"{gene.name}.md"
            rule_path = CLAUDE_DIR / "rules" / f"{gene.name}.md"
            mode_path = CLAUDE_DIR / "modes" / "definitions" / f"{gene.name}.yaml"
            exists = any(p.exists() for p in [skill_path, agent_path, rule_path, mode_path])
            if exists:
                gene._base_fitness = NEUTRAL_FITNESS
                gene.last_invoked = datetime.now(timezone.utc).isoformat()
                count += 1
    return count


def sync_genome_with_disk(genome: Genome) -> dict:
    """Add genes for skills that exist on disk but not in genome.

    Returns: {"added": [...names...], "count": int}
    """
    components = _scan_components()
    protected = _get_protected_set()
    added = []
    for comp in components:
        if comp["name"] not in genome.genes:
            gene = Gene(
                name=comp["name"],
                content_hash=_compute_file_hash(comp["path"]) if "path" in comp else "",
                component_type=comp["type"],
                protected=comp["name"] in protected,
                generation_born=genome.generation,
                last_invoked=datetime.now(timezone.utc).isoformat(),
            )
            genome.genes[comp["name"]] = gene
            added.append(comp["name"])
    return {"added": added, "count": len(added)}


def hydrate_gene(gene: Gene) -> str:
    """Decompress and return gene's content. Falls back to disk read."""
    from lib.converge_dna_core import decompress_content
    if gene.compressed_content:
        return decompress_content(gene.compressed_content)
    # Fallback: read from disk
    skill_path = CLAUDE_DIR / "skills" / gene.name / "SKILL.md"
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    return ""


def dehydrate_gene(gene: Gene, content: str) -> None:
    """Compress content and store in gene. Mutates gene in place."""
    from lib.converge_dna_core import compress_content
    gene.compressed_content = compress_content(content)
    gene.content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]


def populate_genome_content(genome: Genome) -> dict:
    """Walk skills/*/SKILL.md and store compressed content for living skill genes.

    Returns stats dict: {populated: int, skipped: int, total: int}
    """
    stats = {"populated": 0, "skipped": 0, "total": 0}
    for name, gene in genome.genes.items():
        if gene.component_type != "skill":
            stats["skipped"] += 1
            continue
        stats["total"] += 1
        skill_path = CLAUDE_DIR / "skills" / name / "SKILL.md"
        if skill_path.exists():
            content = skill_path.read_text(encoding="utf-8")
            dehydrate_gene(gene, content)
            stats["populated"] += 1
        else:
            stats["skipped"] += 1
    return stats


def bootstrap_genome() -> Genome:
    """Create initial genome by scanning the filesystem."""
    components = _scan_components()
    protected = _get_protected_set()

    genome = Genome(
        generation=0,
        instance_id=str(uuid.uuid4())[:8],
    )

    usage_data = {}
    try:
        metrics_path = CLAUDE_DIR / "metrics.jsonl"
        if metrics_path.exists():
            with open(metrics_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        event = json.loads(line)
                        if event.get("event") in ("skill_triggered", "skill_invoked"):
                            name = event.get("skill", "")
                            if name:
                                usage_data[name] = usage_data.get(name, 0) + 1
                    except json.JSONDecodeError:
                        continue
    except (OSError, IOError):
        pass

    now_iso = datetime.now(timezone.utc).isoformat()

    # Default parameters by component type — unblocks reproduce() point_mutation
    # All values must be numeric (point_mutation does param + delta)
    _default_params = {
        "skill": {"weight": 1.0, "priority": 0.5},
        "agent": {"weight": 1.0, "concurrency": 1.0},
        "mode": {"token_budget": 3000.0},
        "rule": {"strictness": 0.8},
    }

    for comp in components:
        # W2a: Set lineage based on bootstrap source
        lineage_source = (
            "bootstrap:metrics" if comp["name"] in usage_data
            else "bootstrap:filesystem"
        )

        gene = Gene(
            name=comp["name"],
            content_hash=_compute_file_hash(comp["path"]),
            component_type=comp["type"],
            protected=comp["name"] in protected,
            lineage=lineage_source,
            last_invoked=datetime.now(timezone.utc).isoformat(),
        )
        # Initialize last_invoked to creation time, not empty string.
        # Prevents the "30 days stale" default in apply_time_decay()
        # which would kill most genes on first nursery expiration.
        gene.last_invoked = now_iso

        # W2b: Populate default parameters — unblocks genetic mutation
        gene.parameters = dict(_default_params.get(comp["type"], {}))

        use_count = usage_data.get(comp["name"], 0)
        if use_count > 0:
            gene._base_fitness = min(0.9, NEUTRAL_FITNESS + 0.05 * min(use_count, 8))
            gene.invocation_count = use_count
        else:
            gene._base_fitness = NEUTRAL_FITNESS

        if gene.protected:
            gene._base_fitness = max(PROTECTED_FLOOR, gene._base_fitness)

        genome.genes[comp["name"]] = gene

    genome.id = genome._compute_id()

    logger.info(
        "[bootstrap] Created genome gen 0 with %d genes (%d protected)",
        len(genome.genes),
        sum(1 for g in genome.genes.values() if g.protected),
    )

    return genome


# =========================================================================
# Evolutionary Refactor Sync
# =========================================================================

def sync_with_evolutionary_refactor(genome: Genome) -> dict:
    """Bidirectional sync between converge_dna and evolutionary_refactor."""
    stats = {"enriched": 0, "exported": 0, "errors": []}

    try:
        from lib.evolutionary_refactor import scan_components
        components = scan_components()

        for comp in components:
            gene_name = comp.name
            if gene_name in genome.genes:
                gene = genome.genes[gene_name]
                if comp.use_count > gene.invocation_count:
                    gene.invocation_count = comp.use_count
                    stats["enriched"] += 1
                if comp.last_used and comp.last_used != "never":
                    if not gene.last_invoked or comp.last_used > gene.last_invoked:
                        gene.last_invoked = comp.last_used
    except (ImportError, Exception) as e:
        stats["errors"].append(f"import: {e}")

    try:
        bridge_path = CLAUDE_DIR / "cache" / "fitness-bridge.json"
        bridge_data = {}
        for name, gene in genome.genes.items():
            bridge_data[name] = {
                "fitness": round(gene.fitness, 3),
                "tier": gene.tier,
                "invocation_count": gene.invocation_count,
                "kono_permanent": gene.kono_permanent,
                "generation": genome.generation,
            }
            stats["exported"] += 1

        bridge_path.parent.mkdir(parents=True, exist_ok=True)
        with open(bridge_path, "w", encoding="utf-8") as f:
            json.dump(bridge_data, f, indent=2)
    except Exception as e:
        stats["errors"].append(f"export: {e}")

    logger.info(
        "[sync] enriched=%d exported=%d errors=%d",
        stats["enriched"], stats["exported"], len(stats["errors"]),
    )
    return stats


def genome_to_instance_dna(genome: Genome) -> dict:
    """Convert Genome to a KONO InstanceDNA-compatible dict."""
    return {
        "skills": [g.name for g in genome.living_genes()],
        "patterns": {
            f"gene_fitness:{g.name}": round(g.fitness, 4)
            for g in genome.genes.values()
        },
        "preferences": {
            "genome_generation": genome.generation,
            "genome_id": genome.id,
            "genome_success_rate": round(genome.success_rate(), 4),
        },
    }


def instance_dna_to_genome(dna: dict, generation: int = 0) -> Genome:
    """Reconstruct a Genome from a KONO InstanceDNA dict."""
    genome = Genome(generation=generation)

    skill_names = dna.get("skills", [])
    patterns = dna.get("patterns", {})

    for name in skill_names:
        fitness_key = f"gene_fitness:{name}"
        fitness = patterns.get(fitness_key, NEUTRAL_FITNESS)
        gene = Gene(
            name=name,
            _base_fitness=fitness if isinstance(fitness, (int, float)) else NEUTRAL_FITNESS,
        )
        genome.genes[name] = gene

    genome.id = genome._compute_id()
    return genome


# =========================================================================
# Observability
# =========================================================================

def _count_review_verdicts() -> int:
    if not REVIEW_VERDICTS_PATH.exists():
        return 0
    try:
        with open(REVIEW_VERDICTS_PATH, encoding="utf-8", errors="replace") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def genome_health(genome: Genome) -> dict:
    """Report genome health metrics."""
    tier_dist = {"essential": 0, "active": 0, "dormant": 0, "dead": 0}
    for gene in genome.genes.values():
        tier_dist[gene.tier] += 1

    all_contexts = set()
    for gene in genome.genes.values():
        all_contexts.update(gene.expression_contexts)

    sorted_genes = sorted(genome.genes.values(), key=lambda g: g.fitness, reverse=True)

    hitchhikers = [
        g.name for g in genome.genes.values()
        if g.ablation.invoked_total >= 3
        and g.ablation.not_invoked_total >= 3
        and abs(g.ablation.ablation_lift) < 0.05
    ]

    return {
        "total_genes": len(genome.genes),
        "living_genes": len(genome.living_genes()),
        "dead_genes": len(genome.dead_genes()),
        "avg_fitness": round(genome.avg_fitness, 4),
        "tier_distribution": tier_dist,
        "success_rate": round(genome.success_rate(), 4),
        "generation": genome.generation,
        "top_genes": [(g.name, round(g.fitness, 3)) for g in sorted_genes[:10]],
        "bottom_genes": [(g.name, round(g.fitness, 3)) for g in sorted_genes[-5:]],
        "diversity": len(all_contexts),
        "kono_permanent_count": sum(1 for g in genome.genes.values() if g.kono_permanent),
        "domain_coverage": {
            domain: sum(
                1 for g in genome.genes.values()
                if g.domain_fitness.domain_scores.get(domain, 0) > 0.3
            )
            for domain in CUBE_DOMAINS
        },
        "mutation_rate": genome.mutation_rate,
        "hitchhiker_genes": hitchhikers,
        "eval_policy_drift": {
            k: round(genome.eval_policy.get(k) - DEFAULT_EVAL_POLICY.get(k, 0), 4)
            for k in genome.eval_policy.params
            if abs(genome.eval_policy.get(k) - DEFAULT_EVAL_POLICY.get(k, 0)) > 0.001
        },
        "review_verdicts": _count_review_verdicts(),
    }


def cube_genome_health(genome: Genome) -> dict:
    """Report genome health across CUBE domains."""
    domain_report = {}
    for domain in CUBE_DOMAINS:
        genes_in_domain = [
            g for g in genome.genes.values()
            if g.domain_fitness.domain_scores.get(domain, 0) > 0
        ]
        if genes_in_domain:
            avg = sum(
                g.domain_fitness.domain_scores.get(domain, 0)
                for g in genes_in_domain
            ) / len(genes_in_domain)
            top = max(genes_in_domain, key=lambda g: g.domain_fitness.domain_scores.get(domain, 0))
            domain_report[domain] = {
                "avg_fitness": round(avg, 3),
                "gene_count": len(genes_in_domain),
                "top_gene": top.name,
            }
        else:
            domain_report[domain] = {
                "avg_fitness": 0.0,
                "gene_count": 0,
                "top_gene": None,
            }

    all_avgs = {d: r["avg_fitness"] for d, r in domain_report.items()}
    coverage_gaps = [d for d, r in domain_report.items() if r["gene_count"] < 3]

    return {
        **domain_report,
        "coverage_gaps": coverage_gaps,
        "strongest_domain": max(all_avgs, key=all_avgs.get) if all_avgs else None,
        "weakest_domain": min(all_avgs, key=all_avgs.get) if all_avgs else None,
    }


def format_lineage(genome: Genome, depth: int = 5) -> str:
    """Format genome lineage as ASCII tree from history."""
    if not GENOME_HISTORY_PATH.exists():
        return f"Gen {genome.generation} [{genome.id}] {len(genome.genes)} genes, avg={genome.avg_fitness:.2f} (current)"

    snapshots = []
    with open(GENOME_HISTORY_PATH, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                snapshots.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    recent = snapshots[-depth:]
    lines = []
    for i, snap in enumerate(recent):
        marker = " <-- current" if i == len(recent) - 1 else ""
        lines.append(
            f"Gen {snap.get('generation', '?'):>3} [{snap.get('id', '?')[:8]}] "
            f"{snap.get('gene_count', '?')} genes, "
            f"avg={snap.get('avg_fitness', 0):.2f}, "
            f"alive={snap.get('living_count', '?')}, "
            f"success={snap.get('success_rate', 0):.0%}{marker}"
        )

    return "\n".join(lines)


# =========================================================================
# Learning Integration
# =========================================================================

def learning_event_to_mutation(
    genome: Genome,
    candidate: dict,
) -> Genome:
    """Convert an approved learning candidate into a genome insertion."""
    name = candidate.get("name", candidate.get("id", f"learned-{uuid.uuid4().hex[:6]}"))
    gene = Gene(
        name=name,
        component_type="skill",
        lineage=candidate.get("source", "learning-detector"),
    )
    return insertion(genome, gene)


# =========================================================================
# Validation
# =========================================================================

def validate_genome(genome: Genome) -> list[str]:
    """Validate genome integrity. Returns list of error strings (empty = valid)."""
    errors = []

    for name, gene in genome.genes.items():
        if name != gene.name:
            errors.append(f"Gene key '{name}' != gene.name '{gene.name}'")
        if gene._base_fitness < 0.0 or gene._base_fitness > 1.0:
            errors.append(f"Gene '{name}' fitness {gene._base_fitness:.4f} out of [0, 1]")
        if gene.generation_born > genome.generation:
            errors.append(
                f"Gene '{name}' born at gen {gene.generation_born} "
                f"> current gen {genome.generation}"
            )
        if gene.protected and gene._base_fitness < PROTECTED_FLOOR:
            errors.append(
                f"Protected gene '{name}' fitness {gene._base_fitness:.4f} "
                f"< floor {PROTECTED_FLOOR}"
            )
        if gene.kono_permanent and gene._base_fitness < KONO_PERMANENT_FLOOR:
            errors.append(
                f"KONO permanent gene '{name}' fitness {gene._base_fitness:.4f} "
                f"< floor {KONO_PERMANENT_FLOOR}"
            )
        if gene.invocation_count < 0:
            errors.append(f"Gene '{name}' negative invocation_count: {gene.invocation_count}")
        ab = gene.ablation
        if ab.invoked_successes < 0 or ab.invoked_failures < 0:
            errors.append(f"Gene '{name}' negative ablation counters")
        if ab.not_invoked_successes < 0 or ab.not_invoked_failures < 0:
            errors.append(f"Gene '{name}' negative not-invoked ablation counters")

    if genome.generation < 0:
        errors.append(f"Negative generation: {genome.generation}")
    if genome.successful_runs > genome.total_converge_runs:
        errors.append(
            f"successful_runs ({genome.successful_runs}) > "
            f"total_converge_runs ({genome.total_converge_runs})"
        )
    if genome.mutation_rate < 0 or genome.mutation_rate > 1:
        errors.append(f"mutation_rate {genome.mutation_rate} out of [0, 1]")

    return errors


# =========================================================================
# CLI
# =========================================================================

def _require_genome(label: str = "No genome found") -> "Genome":
    """Load genome or exit with error."""
    genome = load_genome()
    if not genome:
        print(f"{label}. Run: converge_dna.py bootstrap")
        raise SystemExit(1)
    return genome


def _cmd_bootstrap() -> None:
    genome = bootstrap_genome()
    save_genome(genome, reason="bootstrap")
    h = genome_health(genome)
    print(f"Bootstrapped genome gen 0: {h['total_genes']} genes")
    print(f"  Tiers: {h['tier_distribution']}")
    print(f"  Avg fitness: {h['avg_fitness']}")


def _cmd_bootstrap_or_load() -> None:
    genome = load_genome()
    if genome:
        apply_time_decay(genome)
        save_genome(genome, reason="decay")
    else:
        genome = bootstrap_genome()
        save_genome(genome, reason="bootstrap")
    h = genome_health(genome)
    print(f"[DNA] Gen {genome.generation} | {h['living_genes']} genes | avg={h['avg_fitness']:.2f}")


def _cmd_health() -> None:
    genome = _require_genome()
    print(json.dumps(genome_health(genome), indent=2, default=str))


def _cmd_cube() -> None:
    genome = _require_genome()
    print(json.dumps(cube_genome_health(genome), indent=2, default=str))


def _cmd_lineage() -> None:
    genome = _require_genome("No genome found")
    print(format_lineage(genome, depth=10))


def _cmd_status() -> None:
    genome = _require_genome("No genome")
    print(f"Genome: gen {genome.generation} [{genome.id}]")
    print(f"  Genes: {len(genome.genes)} ({len(genome.living_genes())} alive, {len(genome.dead_genes())} dead)")
    print(f"  Avg fitness: {genome.avg_fitness:.3f}")
    print(f"  Success rate: {genome.success_rate():.1%} ({genome.successful_runs}/{genome.total_converge_runs})")
    print(f"  KONO permanent: {sum(1 for g in genome.genes.values() if g.kono_permanent)}")


def _cmd_compact() -> None:
    genome = _require_genome("[DNA] No genome")
    h = genome_health(genome)
    t = h["tier_distribution"]
    kono_n = h.get("kono_permanent_count", 0)
    hitch_n = len(h.get("hitchhiker_genes", []))
    parts = [
        f"[DNA] gen:{genome.generation}", f"genes:{h['total_genes']}",
        f"avg:{h['avg_fitness']:.2f}",
        f"E:{t['essential']}/A:{t['active']}/D:{t['dormant']}/X:{t['dead']}",
        f"success:{h['success_rate']:.0%}", f"mut:{genome.mutation_rate:.2f}",
    ]
    if kono_n:
        parts.append(f"kono:{kono_n}")
    if hitch_n:
        parts.append(f"hitch:{hitch_n}")
    print(" ".join(parts))


def _cmd_decay() -> None:
    genome = _require_genome()
    before = genome.avg_fitness
    apply_time_decay(genome)
    save_genome(genome, reason="decay")
    print(f"Time decay applied: avg fitness {before:.3f} -> {genome.avg_fitness:.3f}")


def _cmd_kono_sync() -> None:
    genome = _require_genome()
    dna_snapshot = genome_to_instance_dna(genome)
    bridge_path = CLAUDE_DIR / "cache" / "genome-kono-bridge.json"
    bridge_path.parent.mkdir(parents=True, exist_ok=True)
    with open(bridge_path, "w", encoding="utf-8") as f:
        json.dump(dna_snapshot, f, indent=2)
    print(f"KONO bridge: {len(dna_snapshot.get('skills', []))} skills exported")


def _cmd_sync() -> None:
    genome = _require_genome()
    stats = sync_with_evolutionary_refactor(genome)
    save_genome(genome, reason="sync")
    print(f"Sync: enriched={stats['enriched']} exported={stats['exported']}")
    for err in stats["errors"]:
        print(f"  error: {err}")


def _cmd_validate() -> None:
    genome = _require_genome()
    errors = validate_genome(genome)
    if errors:
        print(f"INVALID: {len(errors)} errors")
        for e in errors:
            print(f"  - {e}")
        raise SystemExit(1)
    print(f"VALID: gen {genome.generation}, {len(genome.genes)} genes")


def _cmd_review_stats() -> None:
    if not REVIEW_VERDICTS_PATH.exists():
        print("No review verdicts recorded yet.")
        return
    verdicts = []
    with open(REVIEW_VERDICTS_PATH, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                verdicts.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    print(f"Review verdicts: {len(verdicts)} total")
    if verdicts:
        attr_counts: dict[str, int] = {}
        neg_counts: dict[str, int] = {}
        genesis_count = 0
        for v in verdicts:
            for gene in v.get("attributions", {}):
                attr_counts[gene] = attr_counts.get(gene, 0) + 1
            for gene in v.get("negative_attributions", {}):
                neg_counts[gene] = neg_counts.get(gene, 0) + 1
            genesis_count += len(v.get("proposed_skills", []))
        top_attr = sorted(attr_counts.items(), key=lambda x: -x[1])[:5]
        print(f"  Genesis proposals: {genesis_count}")
        if top_attr:
            print(f"  Top attributed: {', '.join(f'{n}({c})' for n, c in top_attr)}")
        if neg_counts:
            print(f"  Negatively attributed: {', '.join(f'{n}({c})' for n, c in neg_counts.items())}")


_COMMANDS = {
    "bootstrap": _cmd_bootstrap,
    "bootstrap-or-load": _cmd_bootstrap_or_load,
    "health": _cmd_health,
    "cube": _cmd_cube,
    "lineage": _cmd_lineage,
    "status": _cmd_status,
    "compact": _cmd_compact,
    "decay": _cmd_decay,
    "kono-sync": _cmd_kono_sync,
    "sync": _cmd_sync,
    "validate": _cmd_validate,
    "review-stats": _cmd_review_stats,
}


def main() -> None:
    import sys

    if len(sys.argv) < 2:
        print("Usage: converge_dna.py <command>")
        print(f"Commands: {', '.join(_COMMANDS)}")
        raise SystemExit(1)

    cmd = sys.argv[1]
    handler = _COMMANDS.get(cmd)
    if handler is None:
        print(f"Unknown command: {cmd}")
        raise SystemExit(1)
    handler()


if __name__ == "__main__":
    main()
