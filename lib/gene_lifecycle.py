#!/usr/bin/env python3
"""
gene_lifecycle.py -- Full lifecycle tracking for individual genes.

Tracks genes from birth to extinction with rich biographies, phase detection,
contribution scoring, and formatted memorials.

Usage:
    python3 lib/gene_lifecycle.py biography converge
    python3 lib/gene_lifecycle.py born 5
    python3 lib/gene_lifecycle.py active
    python3 lib/gene_lifecycle.py memorial
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
GENOME_PATH = CLAUDE_DIR / "cache" / "genome.json"
GENOME_HISTORY_PATH = CLAUDE_DIR / "cache" / "genome-history.jsonl"

# Thresholds for phase classification
PEAK_FITNESS = 0.8
DECLINING_DROP = 0.1  # fitness dropped by this much from peak
DORMANT_FITNESS = 0.3
EXTINCT_FITNESS = 0.2
NURSERY_MAX_AGE = 5  # generations before leaving nursery


# =========================================================================
# Enums and Data Structures
# =========================================================================


class GenePhase(Enum):
    """Lifecycle phases for a gene."""

    NURSERY = "nursery"
    GROWING = "growing"
    PEAK = "peak"
    DECLINING = "declining"
    DORMANT = "dormant"
    EXTINCT = "extinct"


@dataclass
class LifecycleEvent:
    """A single event in a gene's lifecycle."""

    generation: int
    event_type: str  # born, invoked, peaked, declined, dormant, extinct, promoted, inherited
    fitness: float
    details: str = ""


@dataclass
class GeneBiography:
    """Complete biography of a gene from birth to current state."""

    name: str
    born_generation: int
    current_phase: GenePhase
    peak_fitness: float
    peak_generation: int
    total_invocations: int
    events: list[LifecycleEvent] = field(default_factory=list)
    contribution_score: float = 0.0
    extinction_cause: Optional[str] = None


# =========================================================================
# History Helpers
# =========================================================================


def load_genome_history(path: Optional[Path] = None) -> list[dict]:
    """Load genome history from JSONL file.

    Each line is a JSON object with generation info and gene snapshots.
    Returns list of dicts sorted by generation.
    """
    history_path = path or GENOME_HISTORY_PATH
    if not history_path.exists():
        return []
    entries = []
    try:
        with open(history_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    entries.sort(key=lambda e: e.get("generation", 0))
    return entries


def load_genome(path: Optional[Path] = None) -> dict:
    """Load current genome from JSON."""
    genome_path = path or GENOME_PATH
    if not genome_path.exists():
        return {}
    try:
        with open(genome_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _get_gene_data(entry: dict) -> dict[str, dict]:
    """Extract gene data from a history entry, handling multiple formats."""
    genes = entry.get("genes", {})
    if isinstance(genes, list):
        return {g["name"]: g for g in genes if isinstance(g, dict) and "name" in g}
    if isinstance(genes, dict):
        return genes
    return {}


def _gene_fitness(gene_data: dict) -> float:
    """Extract fitness from gene data, handling nested structures."""
    if "fitness" in gene_data:
        return float(gene_data["fitness"])
    if "_base_fitness" in gene_data:
        return float(gene_data["_base_fitness"])
    return 0.5


def _gene_invocations(gene_data: dict) -> int:
    """Extract invocation count from gene data."""
    return int(gene_data.get("invocation_count", gene_data.get("invocations", 0)))


def _gene_status(gene_data: dict) -> str:
    """Extract status from gene data."""
    return gene_data.get("status", "active")


# =========================================================================
# Core Functions
# =========================================================================


def detect_phase(
    gene_name: str,
    current_genome: dict,
    genome_history: Optional[list[dict]] = None,
) -> GenePhase:
    """Classify the current lifecycle phase of a gene.

    Args:
        gene_name: Name of the gene to classify.
        current_genome: Current genome dict (with genes key).
        genome_history: Optional history for temporal analysis.

    Returns:
        GenePhase enum value.
    """
    genes = _get_gene_data(current_genome)
    gene = genes.get(gene_name)

    if gene is None:
        # Not in current genome -- check if it ever existed
        if genome_history:
            for entry in genome_history:
                if gene_name in _get_gene_data(entry):
                    return GenePhase.EXTINCT
        return GenePhase.EXTINCT

    fitness = _gene_fitness(gene)
    status = _gene_status(gene)

    if status == "dormant" or fitness < DORMANT_FITNESS:
        if fitness < EXTINCT_FITNESS:
            return GenePhase.EXTINCT
        return GenePhase.DORMANT

    # Determine age
    born_gen = int(gene.get("generation_born", current_genome.get("generation", 0)))
    current_gen = int(current_genome.get("generation", 0))
    age = current_gen - born_gen

    if age <= NURSERY_MAX_AGE:
        return GenePhase.NURSERY

    # Check for peak vs growing vs declining
    peak_fitness = fitness
    if genome_history:
        for entry in genome_history:
            entry_genes = _get_gene_data(entry)
            if gene_name in entry_genes:
                hist_fitness = _gene_fitness(entry_genes[gene_name])
                if hist_fitness > peak_fitness:
                    peak_fitness = hist_fitness

    if peak_fitness - fitness > DECLINING_DROP:
        return GenePhase.DECLINING

    if fitness >= PEAK_FITNESS:
        return GenePhase.PEAK

    return GenePhase.GROWING


def compute_contribution(
    gene_name: str,
    runs: list[dict],
) -> float:
    """Compute how much a gene contributed to successful outcomes.

    Args:
        gene_name: Name of the gene.
        runs: List of run dicts with keys:
            - invoked_genes: list[str]
            - satisfied: bool

    Returns:
        Contribution score as a fraction (0.0 to 1.0).
        Represents the fraction of successful runs that included this gene.
    """
    if not runs:
        return 0.0

    successful_runs = [r for r in runs if r.get("satisfied", False)]
    if not successful_runs:
        return 0.0

    runs_with_gene = sum(
        1
        for r in successful_runs
        if gene_name in r.get("invoked_genes", [])
    )
    return runs_with_gene / len(successful_runs)


@dataclass
class _BiographyState:
    """Mutable state accumulated while tracing a gene through history."""

    events: list[LifecycleEvent] = field(default_factory=list)
    born_gen: int = -1
    peak_fitness: float = 0.0
    peak_gen: int = 0
    total_invocations: int = 0
    last_fitness: float = 0.0
    was_declining: bool = False
    was_dormant: bool = False
    extinction_cause: Optional[str] = None


def _check_extinction_by_removal(
    state: _BiographyState, gen: int,
) -> bool:
    """Check if a missing gene means extinction. Returns True if gene absent."""
    if state.born_gen >= 0 and not state.extinction_cause:
        state.extinction_cause = "removed from genome"
        state.events.append(
            LifecycleEvent(gen, "extinct", 0.0, "Gene removed from genome")
        )
    return True


def _check_birth(
    state: _BiographyState, gen: int, fitness: float, gene: dict,
) -> None:
    """Record birth event on first appearance."""
    if state.born_gen < 0:
        state.born_gen = int(gene.get("generation_born", gen))
        state.events.append(
            LifecycleEvent(state.born_gen, "born", fitness, f"Created in generation {state.born_gen}")
        )


def _check_peak(
    state: _BiographyState, gen: int, fitness: float,
) -> None:
    """Track peak fitness and emit peaked events."""
    if fitness > state.peak_fitness:
        if state.peak_fitness > 0 and fitness >= PEAK_FITNESS:
            state.events.append(
                LifecycleEvent(gen, "peaked", fitness, f"New peak fitness: {fitness:.2f}")
            )
        state.peak_fitness = fitness
        state.peak_gen = gen


def _check_invocations(
    state: _BiographyState, gen: int, fitness: float, invocations: int,
) -> None:
    """Detect invocation milestones."""
    if invocations > state.total_invocations:
        diff = invocations - state.total_invocations
        if diff >= 10 or (state.total_invocations == 0 and diff > 0):
            state.events.append(
                LifecycleEvent(gen, "invoked", fitness, f"Total invocations: {invocations} (+{diff})")
            )
        state.total_invocations = invocations


def _check_health(
    state: _BiographyState, gen: int, fitness: float, status: str,
) -> None:
    """Detect decline, dormancy, extinction, and recovery transitions."""
    if fitness < state.last_fitness - DECLINING_DROP and not state.was_declining:
        state.was_declining = True
        state.events.append(
            LifecycleEvent(gen, "declined", fitness,
                           f"Fitness dropped from {state.last_fitness:.2f} to {fitness:.2f}")
        )

    if (status == "dormant" or fitness < DORMANT_FITNESS) and not state.was_dormant:
        state.was_dormant = True
        state.events.append(
            LifecycleEvent(gen, "dormant", fitness, f"Gene became dormant (fitness: {fitness:.2f})")
        )

    if fitness < EXTINCT_FITNESS and not state.extinction_cause:
        state.extinction_cause = f"fitness below {EXTINCT_FITNESS}"
        state.events.append(
            LifecycleEvent(gen, "extinct", fitness, "Fitness dropped below extinction threshold")
        )

    if state.was_declining and fitness > state.last_fitness + DECLINING_DROP / 2:
        state.was_declining = False
    if state.was_dormant and fitness >= DORMANT_FITNESS and status != "dormant":
        state.was_dormant = False


def _classify_final_phase(
    state: _BiographyState, genome_history: list[dict],
) -> GenePhase:
    """Determine current phase from accumulated state."""
    if state.extinction_cause:
        return GenePhase.EXTINCT
    if state.was_dormant:
        return GenePhase.DORMANT
    if state.was_declining:
        return GenePhase.DECLINING
    if state.born_gen >= 0 and genome_history:
        last_gen = int(genome_history[-1].get("generation", 0))
        age = last_gen - state.born_gen
        if age <= NURSERY_MAX_AGE:
            return GenePhase.NURSERY
        if state.peak_fitness >= PEAK_FITNESS and abs(state.last_fitness - state.peak_fitness) < DECLINING_DROP:
            return GenePhase.PEAK
        return GenePhase.GROWING
    return GenePhase.EXTINCT


def build_biography(
    gene_name: str,
    genome_history: list[dict],
    runs: Optional[list[dict]] = None,
) -> GeneBiography:
    """Construct full biography of a gene from genome history.

    Args:
        gene_name: Name of the gene to trace.
        genome_history: Sorted list of genome snapshots.
        runs: Optional list of converge run outcomes for contribution scoring.

    Returns:
        GeneBiography with complete lifecycle events.
    """
    state = _BiographyState()

    for entry in genome_history:
        gen = int(entry.get("generation", 0))
        gene = _get_gene_data(entry).get(gene_name)

        if gene is None:
            _check_extinction_by_removal(state, gen)
            continue

        fitness = _gene_fitness(gene)
        _check_birth(state, gen, fitness, gene)
        _check_peak(state, gen, fitness)
        _check_invocations(state, gen, fitness, _gene_invocations(gene))
        _check_health(state, gen, fitness, _gene_status(gene))
        state.last_fitness = fitness

    return GeneBiography(
        name=gene_name,
        born_generation=max(state.born_gen, 0),
        current_phase=_classify_final_phase(state, genome_history),
        peak_fitness=state.peak_fitness,
        peak_generation=state.peak_gen,
        total_invocations=state.total_invocations,
        events=state.events,
        contribution_score=compute_contribution(gene_name, runs) if runs else 0.0,
        extinction_cause=state.extinction_cause,
    )


def find_born_in(generation: int, genome_history: list[dict]) -> list[str]:
    """Find genes that were first created in a specific generation.

    Args:
        generation: The generation number to query.
        genome_history: Sorted list of genome snapshots.

    Returns:
        List of gene names born in the given generation.
    """
    seen_before: set[str] = set()
    born_in_gen: list[str] = []

    for entry in genome_history:
        gen = int(entry.get("generation", 0))
        genes = _get_gene_data(entry)

        if gen < generation:
            seen_before.update(genes.keys())
            continue

        if gen == generation:
            for name, data in genes.items():
                gene_born = int(data.get("generation_born", gen))
                if name not in seen_before and gene_born == generation:
                    born_in_gen.append(name)
                elif name not in seen_before:
                    # Gene appeared here but claims an earlier birth
                    # Trust generation_born if available
                    if gene_born == generation:
                        born_in_gen.append(name)
            break

        if gen > generation:
            break

    return sorted(born_in_gen)


def find_active(genome: dict) -> list[dict]:
    """Find currently active genes with their stats.

    Args:
        genome: Current genome dict.

    Returns:
        List of dicts with name, fitness, invocations, phase info.
    """
    genes = _get_gene_data(genome)
    active: list[dict] = []

    for name, data in genes.items():
        fitness = _gene_fitness(data)
        status = _gene_status(data)
        invocations = _gene_invocations(data)

        if status != "dormant" and fitness >= DORMANT_FITNESS:
            active.append({
                "name": name,
                "fitness": round(fitness, 4),
                "invocations": invocations,
                "status": status,
                "tier": _classify_tier(fitness),
            })

    active.sort(key=lambda g: (-g["fitness"], g["name"]))
    return active


def find_extinct(genome_history: list[dict]) -> list[GeneBiography]:
    """Find extinct genes and build their biographies.

    A gene is extinct if it appeared in history but is absent from the
    last genome snapshot, or its fitness dropped below the extinction threshold.

    Args:
        genome_history: Sorted list of genome snapshots.

    Returns:
        List of GeneBiography for each extinct gene.
    """
    if not genome_history:
        return []

    # Collect all genes ever seen
    all_genes: set[str] = set()
    for entry in genome_history:
        all_genes.update(_get_gene_data(entry).keys())

    # Check which are absent or extinct in the last snapshot
    last_entry = genome_history[-1]
    last_genes = _get_gene_data(last_entry)

    extinct_names: list[str] = []
    for name in all_genes:
        if name not in last_genes:
            extinct_names.append(name)
        else:
            fitness = _gene_fitness(last_genes[name])
            status = _gene_status(last_genes[name])
            if fitness < EXTINCT_FITNESS or status == "dead":
                extinct_names.append(name)

    extinct: list[GeneBiography] = []
    for name in sorted(extinct_names):
        bio = build_biography(name, genome_history)
        extinct.append(bio)

    return extinct


def _classify_tier(fitness: float) -> str:
    """Classify gene tier from fitness value."""
    if fitness >= 0.8:
        return "essential"
    if fitness >= 0.5:
        return "active"
    if fitness >= EXTINCT_FITNESS:
        return "dormant"
    return "dead"


# =========================================================================
# Formatting
# =========================================================================


def format_biography(bio: GeneBiography) -> str:
    """Format a gene biography for human-readable display.

    Args:
        bio: GeneBiography to format.

    Returns:
        Multi-line formatted string.
    """
    lines = [
        f"Gene: {bio.name}",
        f"Born: Gen {bio.born_generation} | Phase: {bio.current_phase.value.upper()} | Fitness: {bio.peak_fitness:.2f}",
        f"Peak: {bio.peak_fitness:.2f} at Gen {bio.peak_generation} | Invocations: {bio.total_invocations}",
    ]

    if bio.contribution_score > 0:
        lines.append(f"Contribution: {bio.contribution_score:.0%} of successful runs included this gene")

    if bio.extinction_cause:
        lines.append(f"Extinction: {bio.extinction_cause}")

    # Timeline
    if bio.events:
        phases = []
        for event in bio.events:
            if event.event_type == "born":
                phases.append("born")
            elif event.event_type == "peaked":
                phases.append(f"peak (Gen {event.generation})")
            elif event.event_type == "declined":
                phases.append("decline")
            elif event.event_type == "dormant":
                phases.append("dormant")
            elif event.event_type == "extinct":
                phases.append("extinct")
        if phases:
            lines.append(f"Timeline: {' -> '.join(phases)}")

    return "\n".join(lines)


def format_memorial(extincts: list[GeneBiography]) -> str:
    """Format a memorial for extinct genes.

    Args:
        extincts: List of extinct gene biographies.

    Returns:
        Formatted memorial string.
    """
    if not extincts:
        return "No extinct genes found. All genes survive."

    lines = [
        "Gene Memorial",
        "=" * 50,
        "",
    ]

    for bio in extincts:
        duration = ""
        if bio.events:
            first_gen = bio.events[0].generation
            last_gen = bio.events[-1].generation
            duration = f" ({last_gen - first_gen} generations)"

        lines.append(f"  {bio.name}")
        lines.append(f"    Born: Gen {bio.born_generation} | Peak: {bio.peak_fitness:.2f} at Gen {bio.peak_generation}{duration}")
        lines.append(f"    Invocations: {bio.total_invocations} | Cause: {bio.extinction_cause or 'unknown'}")
        lines.append("")

    lines.append(f"Total extinct: {len(extincts)}")
    return "\n".join(lines)


# =========================================================================
# CLI
# =========================================================================


def _cli_biography(gene_name: str) -> None:
    """CLI handler for biography command."""
    history = load_genome_history()
    if not history:
        print(f"No genome history found at {GENOME_HISTORY_PATH}")
        return
    bio = build_biography(gene_name, history)
    print(format_biography(bio))


def _cli_born(generation: int) -> None:
    """CLI handler for born command."""
    history = load_genome_history()
    if not history:
        print(f"No genome history found at {GENOME_HISTORY_PATH}")
        return
    genes = find_born_in(generation, history)
    if genes:
        print(f"Genes born in Generation {generation}:")
        for name in genes:
            print(f"  {name}")
    else:
        print(f"No genes born in Generation {generation}")


def _cli_active() -> None:
    """CLI handler for active command."""
    genome = load_genome()
    if not genome:
        print(f"No genome found at {GENOME_PATH}")
        return
    active = find_active(genome)
    if not active:
        print("No active genes found.")
        return
    print(f"Active genes ({len(active)}):")
    print(f"{'Name':<30} {'Fitness':>8} {'Invocations':>12} {'Tier':<10}")
    print("-" * 65)
    for g in active:
        print(f"{g['name']:<30} {g['fitness']:>8.4f} {g['invocations']:>12} {g['tier']:<10}")


def _cli_memorial() -> None:
    """CLI handler for memorial command."""
    history = load_genome_history()
    if not history:
        print(f"No genome history found at {GENOME_HISTORY_PATH}")
        return
    extincts = find_extinct(history)
    print(format_memorial(extincts))


def main() -> None:
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: gene_lifecycle.py {biography <gene>|born <gen>|active|memorial}")
        sys.exit(1)

    command = sys.argv[1]

    if command == "biography":
        if len(sys.argv) < 3:
            print("Usage: gene_lifecycle.py biography <gene_name>")
            sys.exit(1)
        _cli_biography(sys.argv[2])
    elif command == "born":
        if len(sys.argv) < 3:
            print("Usage: gene_lifecycle.py born <generation>")
            sys.exit(1)
        try:
            gen = int(sys.argv[2])
        except ValueError:
            print(f"Invalid generation number: {sys.argv[2]}")
            sys.exit(1)
        _cli_born(gen)
    elif command == "active":
        _cli_active()
    elif command == "memorial":
        _cli_memorial()
    else:
        print(f"Unknown command: {command}")
        print("Usage: gene_lifecycle.py {biography <gene>|born <gen>|active|memorial}")
        sys.exit(1)


if __name__ == "__main__":
    main()
