"""
converge_dna_operators.py — Genetic operators for the evolutionary DNA layer.

Pure genetic operations: mutation, insertion, deletion, crossover,
tournament selection, and reproduction. All return new Genome instances.

Depends on: converge_dna_core (data structures only).
"""

from __future__ import annotations

import logging
import sys
from copy import deepcopy
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.converge_dna_core import (
    DEAD_THRESHOLD,
    NEUTRAL_FITNESS,
    Gene,
    Genome,
)
import re

logger = logging.getLogger(__name__)


# =========================================================================
# Mutation Operators (all return new Genome instances)
# =========================================================================

def point_mutation(
    genome: Genome,
    gene_name: str,
    param_key: str,
    delta: float,
) -> Genome:
    """Tweak one parameter of one gene. Returns new Genome."""
    if gene_name not in genome.genes:
        raise KeyError(f"Gene not found: {gene_name}")

    new_genome = deepcopy(genome)
    gene = new_genome.genes[gene_name]

    if param_key not in gene.parameters:
        gene.parameters[param_key] = 0.0

    gene.parameters[param_key] = round(gene.parameters[param_key] + delta, 4)
    gene.mutations += 1
    gene.content_hash = ""

    new_genome.generation += 1
    new_genome.parent_id = genome.id
    new_genome.id = new_genome._compute_id()

    return new_genome


def insertion(genome: Genome, new_gene: Gene) -> Genome:
    """Add a new gene to the genome. Returns new Genome."""
    if new_gene.name in genome.genes:
        raise ValueError(f"Gene already exists: {new_gene.name}")

    new_genome = deepcopy(genome)
    new_gene = deepcopy(new_gene)
    new_gene.generation_born = new_genome.generation + 1
    new_gene._base_fitness = NEUTRAL_FITNESS
    new_genome.genes[new_gene.name] = new_gene

    new_genome.generation += 1
    new_genome.parent_id = genome.id
    new_genome.id = new_genome._compute_id()

    return new_genome


def deletion(genome: Genome, gene_name: str) -> Genome:
    """Remove a dead gene from the genome. Returns new Genome.

    Only genes in the Dead tier (fitness <= 0.2) can be deleted.
    Protected genes cannot be deleted.
    """
    if gene_name not in genome.genes:
        raise KeyError(f"Gene not found: {gene_name}")

    gene = genome.genes[gene_name]
    if gene.protected:
        raise ValueError(f"Cannot delete protected gene: {gene_name}")
    if gene.fitness > DEAD_THRESHOLD:
        raise ValueError(
            f"Cannot delete gene with fitness {gene.fitness:.2f} > {DEAD_THRESHOLD}. "
            "Only dead-tier genes can be deleted."
        )

    new_genome = deepcopy(genome)
    del new_genome.genes[gene_name]

    new_genome.generation += 1
    new_genome.parent_id = genome.id
    new_genome.id = new_genome._compute_id()

    return new_genome


def crossover(
    genome_a: Genome,
    genome_b: Genome,
    fitness_threshold: float = 0.6,
) -> Genome:
    """Merge high-fitness genes from two genomes.

    1. Start with genome_a as the base.
    2. For each gene in genome_b NOT in genome_a: add if fit enough.
    3. For genes in BOTH: keep the variant with higher fitness.
    """
    new_genome = deepcopy(genome_a)

    for name, gene_b in genome_b.genes.items():
        if name not in new_genome.genes:
            if gene_b.fitness >= fitness_threshold:
                new_gene = deepcopy(gene_b)
                new_gene.lineage = f"crossover:{genome_b.id}"
                new_genome.genes[name] = new_gene
        else:
            if gene_b.fitness > new_genome.genes[name].fitness:
                new_gene = deepcopy(gene_b)
                new_gene.lineage = f"crossover:{genome_b.id}"
                new_genome.genes[name] = new_gene

    new_genome.generation = max(genome_a.generation, genome_b.generation) + 1
    new_genome.parent_id = f"{genome_a.id}x{genome_b.id}"
    new_genome.id = new_genome._compute_id()

    return new_genome


# =========================================================================
# Selection & Reproduction
# =========================================================================

def select(population: list[Genome], keep: int) -> list[Genome]:
    """Tournament selection: keep top N genomes by average living gene fitness."""
    def rank_key(g: Genome) -> tuple[float, float, int]:
        return (g.avg_fitness, g.success_rate(), len(g.living_genes()))

    ranked = sorted(population, key=rank_key, reverse=True)
    return ranked[:keep]


def thompson_select(
    population: list[Genome],
    n_parents: int = 2,
    prior_alpha: float = 1.0,
    prior_beta: float = 1.0,
) -> list[Genome]:
    """Select parents using Thompson sampling for exploration-exploitation balance.

    Models each genome's quality as Beta(alpha, beta) where:
    - alpha = prior_alpha + (fitness * invocations)  [successes]
    - beta = prior_beta + ((1 - fitness) * invocations)  [failures]

    Genomes with few invocations have wide distributions (uncertainty),
    encouraging exploration. Well-tested high-fitness genomes have narrow
    distributions near their true fitness.

    Inspired by AutoHarness (arXiv:2603.03329v1) which uses Thompson sampling
    for tree search during code harness synthesis.

    Args:
        population: List of Genome instances.
        n_parents: Number of parents to select.
        prior_alpha: Beta prior for successes (default 1.0 = uniform prior).
        prior_beta: Beta prior for failures (default 1.0 = uniform prior).

    Returns:
        List of n_parents Genome instances selected via Thompson sampling.
    """
    import random as _rng

    if not population:
        return []
    if len(population) <= n_parents:
        return list(population)

    samples = []
    for genome in population:
        living = genome.living_genes()
        if not living:
            samples.append((0.0, genome))
            continue

        # Compute invocation count as proxy for "trials"
        total_invocations = sum(
            g.invocation_count for g in living
        )
        avg_fit = genome.avg_fitness

        # Map to Beta distribution parameters
        # More invocations = tighter distribution around true fitness
        effective_trials = max(total_invocations, 1)
        alpha = prior_alpha + (avg_fit * effective_trials)
        beta = prior_beta + ((1.0 - avg_fit) * effective_trials)

        # Sample from Beta distribution
        sample = _rng.betavariate(alpha, beta)
        samples.append((sample, genome))

    # Sort by sampled value (descending) and take top n_parents
    samples.sort(key=lambda x: x[0], reverse=True)
    return [genome for _, genome in samples[:n_parents]]


def reproduce(
    population: list[Genome],
    mutation_rate: float = 0.1,
    crossover_rate: float = 0.2,
    use_thompson: bool = False,
) -> list[Genome]:
    """Generate offspring genomes via crossover and mutation.

    Uses each parent genome's adaptive mutation_rate when available.
    Also mutates the evaluation policy — the offspring may evaluate
    fitness differently than the parents.

    Args:
        population: List of Genome instances.
        mutation_rate: Base mutation rate (overridden by genome's adaptive rate).
        crossover_rate: Probability of crossover between parent pairs.
        use_thompson: If True, use Thompson sampling for parent selection
            instead of sequential adjacent pairing.
    """
    import random
    offspring = []

    if use_thompson:
        # Thompson sampling: explore underexplored genomes
        n_pairs = max(1, len(population) // 2)
        for _ in range(n_pairs):
            parents = thompson_select(population, n_parents=2)
            if len(parents) < 2:
                continue
            if random.random() < crossover_rate:
                child = crossover(parents[0], parents[1])

                effective_rate = parents[0].mutation_rate or mutation_rate

                for gene_name, gene in list(child.genes.items()):
                    if random.random() < effective_rate and gene.parameters:
                        param_key = random.choice(list(gene.parameters.keys()))
                        delta = random.gauss(0, 0.05)
                        try:
                            child = point_mutation(child, gene_name, param_key, delta)
                        except (KeyError, ValueError):
                            pass

                fitter_parent = (
                    parents[0]
                    if parents[0].avg_fitness >= parents[1].avg_fitness
                    else parents[1]
                )
                child.eval_policy = fitter_parent.eval_policy.mutate(
                    mutation_strength=effective_rate * 0.1
                )

                child.mutation_rate = fitter_parent.mutation_rate * (
                    1.0 + random.gauss(0, 0.05)
                )
                child.mutation_rate = round(
                    max(0.01, min(0.5, child.mutation_rate)), 4
                )

                offspring.append(child)
    else:
        # Original sequential pairing
        for i in range(len(population) - 1):
            if random.random() < crossover_rate:
                child = crossover(population[i], population[i + 1])

                effective_rate = population[i].mutation_rate or mutation_rate

                for gene_name, gene in list(child.genes.items()):
                    if random.random() < effective_rate and gene.parameters:
                        param_key = random.choice(list(gene.parameters.keys()))
                        delta = random.gauss(0, 0.05)
                        try:
                            child = point_mutation(child, gene_name, param_key, delta)
                        except (KeyError, ValueError):
                            pass

                fitter_parent = (
                    population[i]
                    if population[i].avg_fitness >= population[i + 1].avg_fitness
                    else population[i + 1]
                )
                child.eval_policy = fitter_parent.eval_policy.mutate(
                    mutation_strength=effective_rate * 0.1
                )

                child.mutation_rate = fitter_parent.mutation_rate * (
                    1.0 + random.gauss(0, 0.05)
                )
                child.mutation_rate = round(
                    max(0.01, min(0.5, child.mutation_rate)), 4
                )

                offspring.append(child)

    return offspring


# =========================================================================
# Splitting Operator (Phase 3: Micro-Skills)
# =========================================================================

def _slugify(text: str) -> str:
    """Convert a section heading to a URL-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    return slug.strip('-')


def split_gene(
    parent: Gene,
    content: str,
    fitness_discount: float = 0.8,
) -> list[Gene]:
    """Split a monolith gene into child micro-genes based on ## sections.

    Extracts text between ## headings. Each child inherits:
    - fitness = parent fitness * fitness_discount
    - lineage = "split:{parent_name}:{section_slug}"
    - name = "{parent_name}-{section_slug}"
    - component_type from parent

    Args:
        parent: The parent Gene to split.
        content: Full text of the SKILL.md file.
        fitness_discount: Multiplier for child fitness (default 0.8).

    Returns:
        List of child Gene objects. NOT added to genome automatically.
    """
    # Split content by ## headings (not ### or deeper)
    parts = re.split(r'^(## .+)$', content, flags=re.MULTILINE)

    # parts looks like: [preamble, "## Heading1", body1, "## Heading2", body2, ...]
    children = []
    i = 1  # skip preamble
    while i < len(parts) - 1:
        heading = parts[i].lstrip('#').strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        section_slug = _slugify(heading)

        child = Gene(
            name=f"{parent.name}-{section_slug}",
            content_hash="",
            component_type=parent.component_type,
            _base_fitness=round(parent.fitness * fitness_discount, 4),
            lineage=f"split:{parent.name}:{section_slug}",
            generation_born=parent.generation_born,
        )
        # Store section content in parameters for downstream use
        child.parameters["section_content"] = body.strip()

        children.append(child)
        i += 2

    return children
