#!/usr/bin/env python3
"""Detect and catalog gene mutations across generations.

Scans genome history snapshots to identify fitness jumps, invocation spikes,
new co-expression partners, status changes, and behavioral shifts. Each
mutation is classified as beneficial, neutral, or harmful based on
correlation with converge-metrics run scores.

CLI: mutations | mutations <gene> | beneficial | harmful | rate
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class MutationType(Enum):
    """Categories of observable gene mutations."""

    FITNESS_JUMP = "fitness_jump"
    FITNESS_DROP = "fitness_drop"
    INVOCATION_SPIKE = "invocation_spike"
    NEW_PARTNER = "new_partner"
    STATUS_CHANGE = "status_change"
    BEHAVIOR_SHIFT = "behavior_shift"


class Classification(Enum):
    """Mutation impact classification."""

    BENEFICIAL = "beneficial"
    NEUTRAL = "neutral"
    HARMFUL = "harmful"
    UNKNOWN = "unknown"


@dataclass
class Mutation:
    """A single observed gene mutation."""

    gene_name: str
    generation: int
    mutation_type: MutationType
    magnitude: float
    classification: Classification = Classification.UNKNOWN
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "gene_name": self.gene_name,
            "generation": self.generation,
            "mutation_type": self.mutation_type.value,
            "magnitude": round(self.magnitude, 4),
            "classification": self.classification.value,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _genes_by_name(generation_snapshot: dict) -> dict[str, dict]:
    """Index genes by name from a generation snapshot."""
    return {g["name"]: g for g in generation_snapshot.get("genes", [])}


def _get_partners(gene_name: str, runs: list[dict]) -> set[str]:
    """Extract co-expression partners for a gene from run data."""
    partners: set[str] = set()
    for run in runs:
        genes_in_run = run.get("genes_active", run.get("genes", []))
        names = {g if isinstance(g, str) else g.get("name", "") for g in genes_in_run}
        if gene_name in names:
            partners.update(names - {gene_name})
    return partners


def _runs_for_generation(runs: list[dict] | None, gen: int) -> list[dict]:
    """Filter runs that belong to a specific generation."""
    if not runs:
        return []
    return [r for r in runs if r.get("generation") == gen]


def _average_score(runs: list[dict]) -> float | None:
    """Average score across runs; returns None if no scores."""
    scores = [r["score"] for r in runs if "score" in r]
    if not scores:
        return None
    return sum(scores) / len(scores)


# ---------------------------------------------------------------------------
# Individual mutation detectors
# ---------------------------------------------------------------------------


def detect_fitness_jump(
    gene: dict, prev_gen: dict, curr_gen: dict, threshold: float = 0.2
) -> Mutation | None:
    """Detect a positive fitness change exceeding threshold."""
    prev = prev_gen.get("fitness", 0.0)
    curr = curr_gen.get("fitness", 0.0)
    delta = curr - prev
    if delta > threshold:
        return Mutation(
            gene_name=gene["name"],
            generation=curr_gen.get("_generation", 0),
            mutation_type=MutationType.FITNESS_JUMP,
            magnitude=delta,
            details=f"fitness {prev:.4f} -> {curr:.4f} (+{delta:.4f})",
        )
    return None


def detect_fitness_drop(
    gene: dict, prev_gen: dict, curr_gen: dict, threshold: float = 0.2
) -> Mutation | None:
    """Detect a negative fitness change exceeding threshold."""
    prev = prev_gen.get("fitness", 0.0)
    curr = curr_gen.get("fitness", 0.0)
    delta = prev - curr
    if delta > threshold:
        return Mutation(
            gene_name=gene["name"],
            generation=curr_gen.get("_generation", 0),
            mutation_type=MutationType.FITNESS_DROP,
            magnitude=delta,
            details=f"fitness {prev:.4f} -> {curr:.4f} (-{delta:.4f})",
        )
    return None


def detect_invocation_spike(
    gene: dict, prev_gen: dict, curr_gen: dict, threshold: float = 3.0
) -> Mutation | None:
    """Detect an invocation count jump exceeding threshold multiplier."""
    prev = prev_gen.get("invocations", 0)
    curr = curr_gen.get("invocations", 0)
    if prev == 0:
        if curr > 0:
            return Mutation(
                gene_name=gene["name"],
                generation=curr_gen.get("_generation", 0),
                mutation_type=MutationType.INVOCATION_SPIKE,
                magnitude=float(curr),
                details=f"invocations 0 -> {curr} (new activity)",
            )
        return None
    ratio = curr / prev
    if ratio >= threshold:
        return Mutation(
            gene_name=gene["name"],
            generation=curr_gen.get("_generation", 0),
            mutation_type=MutationType.INVOCATION_SPIKE,
            magnitude=ratio,
            details=f"invocations {prev} -> {curr} ({ratio:.1f}x)",
        )
    return None


def detect_new_partner(
    gene: dict, prev_runs: list[dict], curr_runs: list[dict]
) -> list[Mutation]:
    """Detect new co-expression partners that appeared in current generation."""
    name = gene["name"]
    prev_partners = _get_partners(name, prev_runs)
    curr_partners = _get_partners(name, curr_runs)
    new = curr_partners - prev_partners
    mutations = []
    for partner in sorted(new):
        gen = 0
        if curr_runs:
            gen = curr_runs[0].get("generation", 0)
        mutations.append(
            Mutation(
                gene_name=name,
                generation=gen,
                mutation_type=MutationType.NEW_PARTNER,
                magnitude=1.0,
                details=f"new co-expression partner: {partner}",
            )
        )
    return mutations


def detect_status_change(
    gene: dict, prev_gen: dict, curr_gen: dict
) -> Mutation | None:
    """Detect gene status transitions (active, dormant, candidate, etc.)."""
    prev_status = prev_gen.get("status", "unknown")
    curr_status = curr_gen.get("status", "unknown")
    if prev_status != curr_status:
        return Mutation(
            gene_name=gene["name"],
            generation=curr_gen.get("_generation", 0),
            mutation_type=MutationType.STATUS_CHANGE,
            magnitude=1.0,
            details=f"status {prev_status} -> {curr_status}",
        )
    return None


def detect_behavior_shift(
    gene: dict, prev_gen: dict, curr_gen: dict
) -> Mutation | None:
    """Detect changes in gene behavior metadata (tags, weight, tier)."""
    shift_fields = ["weight", "tier", "tags", "category"]
    changes = []
    for fld in shift_fields:
        prev_val = prev_gen.get(fld)
        curr_val = curr_gen.get(fld)
        if prev_val is not None and curr_val is not None and prev_val != curr_val:
            changes.append(f"{fld}: {prev_val} -> {curr_val}")
    if changes:
        return Mutation(
            gene_name=gene["name"],
            generation=curr_gen.get("_generation", 0),
            mutation_type=MutationType.BEHAVIOR_SHIFT,
            magnitude=float(len(changes)),
            details="; ".join(changes),
        )
    return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_mutation(mutation: Mutation, runs: list[dict] | None = None) -> Mutation:
    """Classify a mutation as beneficial, neutral, or harmful.

    - FITNESS_JUMP -> beneficial
    - FITNESS_DROP -> harmful
    - STATUS_CHANGE dormant->active -> beneficial, active->dormant -> harmful
    - INVOCATION_SPIKE -> beneficial if correlated with higher scores
    - NEW_PARTNER -> beneficial if correlated with higher scores
    - BEHAVIOR_SHIFT -> neutral by default
    """
    mt = mutation.mutation_type

    if mt == MutationType.FITNESS_JUMP:
        mutation.classification = Classification.BENEFICIAL
        return mutation

    if mt == MutationType.FITNESS_DROP:
        mutation.classification = Classification.HARMFUL
        return mutation

    if mt == MutationType.STATUS_CHANGE:
        if "dormant -> active" in mutation.details or "candidate -> active" in mutation.details:
            mutation.classification = Classification.BENEFICIAL
        elif "active -> dormant" in mutation.details or "active -> removed" in mutation.details:
            mutation.classification = Classification.HARMFUL
        else:
            mutation.classification = Classification.NEUTRAL
        return mutation

    if mt == MutationType.BEHAVIOR_SHIFT:
        mutation.classification = Classification.NEUTRAL
        return mutation

    # For INVOCATION_SPIKE and NEW_PARTNER, check run correlation
    if runs:
        gen = mutation.generation
        gen_runs = _runs_for_generation(runs, gen)
        prev_runs = _runs_for_generation(runs, gen - 1)
        curr_avg = _average_score(gen_runs)
        prev_avg = _average_score(prev_runs)
        if curr_avg is not None and prev_avg is not None:
            if curr_avg > prev_avg:
                mutation.classification = Classification.BENEFICIAL
            elif curr_avg < prev_avg:
                mutation.classification = Classification.HARMFUL
            else:
                mutation.classification = Classification.NEUTRAL
            return mutation

    mutation.classification = Classification.UNKNOWN
    return mutation


# ---------------------------------------------------------------------------
# Main detection engine
# ---------------------------------------------------------------------------


def detect_mutations(
    genome_history: list[dict], runs: list[dict] | None = None
) -> list[Mutation]:
    """Scan genome history for all mutations across generations.

    Args:
        genome_history: List of generation snapshots, each containing
            {"generation": N, "genes": [{"name": ..., "fitness": ..., ...}]}
        runs: Optional list of converge-metrics JSONL entries for
            score-based classification.

    Returns:
        List of detected Mutation objects, sorted by generation then gene name.
    """
    if len(genome_history) < 2:
        return []

    sorted_history = sorted(genome_history, key=lambda h: h.get("generation", 0))
    all_mutations: list[Mutation] = []

    for i in range(1, len(sorted_history)):
        prev_snap = sorted_history[i - 1]
        curr_snap = sorted_history[i]
        prev_genes = _genes_by_name(prev_snap)
        curr_genes = _genes_by_name(curr_snap)
        curr_gen_num = curr_snap.get("generation", i)

        prev_gen_runs = _runs_for_generation(runs, prev_snap.get("generation", i - 1))
        curr_gen_runs = _runs_for_generation(runs, curr_gen_num)

        all_gene_names = sorted(set(prev_genes.keys()) | set(curr_genes.keys()))

        for name in all_gene_names:
            prev_g = prev_genes.get(name, {})
            curr_g = curr_genes.get(name, {})
            gene_ref = {"name": name}

            # Annotate with generation number for detectors
            prev_g_ann = {**prev_g, "_generation": prev_snap.get("generation", i - 1)}
            curr_g_ann = {**curr_g, "_generation": curr_gen_num}

            # Fitness mutations
            m = detect_fitness_jump(gene_ref, prev_g_ann, curr_g_ann)
            if m:
                all_mutations.append(m)
            m = detect_fitness_drop(gene_ref, prev_g_ann, curr_g_ann)
            if m:
                all_mutations.append(m)

            # Invocation spike
            m = detect_invocation_spike(gene_ref, prev_g_ann, curr_g_ann)
            if m:
                all_mutations.append(m)

            # Status change
            m = detect_status_change(gene_ref, prev_g_ann, curr_g_ann)
            if m:
                all_mutations.append(m)

            # Behavior shift
            m = detect_behavior_shift(gene_ref, prev_g_ann, curr_g_ann)
            if m:
                all_mutations.append(m)

            # New partner detection (requires runs)
            if runs:
                partner_mutations = detect_new_partner(
                    gene_ref, prev_gen_runs, curr_gen_runs
                )
                all_mutations.extend(partner_mutations)

    # Classify all mutations
    for mut in all_mutations:
        classify_mutation(mut, runs)

    # Sort by generation, then gene name
    all_mutations.sort(key=lambda m: (m.generation, m.gene_name))
    return all_mutations


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def mutation_rate(mutations: list[Mutation], generations: int) -> float:
    """Calculate mutations per generation.

    Args:
        mutations: List of detected mutations.
        generations: Number of generations spanned.

    Returns:
        Mutations per generation as a float. Returns 0.0 if generations <= 0.
    """
    if generations <= 0:
        return 0.0
    return len(mutations) / generations


def filter_by_classification(
    mutations: list[Mutation], classification: Classification
) -> list[Mutation]:
    """Filter mutations by classification."""
    return [m for m in mutations if m.classification == classification]


def filter_by_gene(mutations: list[Mutation], gene_name: str) -> list[Mutation]:
    """Filter mutations for a specific gene."""
    return [m for m in mutations if m.gene_name == gene_name]


def filter_by_type(
    mutations: list[Mutation], mutation_type: MutationType
) -> list[Mutation]:
    """Filter mutations by type."""
    return [m for m in mutations if m.mutation_type == mutation_type]


def unique_genes(mutations: list[Mutation]) -> list[str]:
    """Get sorted list of unique gene names from mutations."""
    return sorted({m.gene_name for m in mutations})


def mutations_per_gene(mutations: list[Mutation]) -> dict[str, int]:
    """Count mutations per gene."""
    counts: dict[str, int] = {}
    for m in mutations:
        counts[m.gene_name] = counts.get(m.gene_name, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def most_mutated(mutations: list[Mutation], top_n: int = 5) -> list[tuple[str, int]]:
    """Return the top N most mutated genes."""
    counts = mutations_per_gene(mutations)
    return list(counts.items())[:top_n]


def generation_span(mutations: list[Mutation]) -> tuple[int, int]:
    """Return (min_generation, max_generation) from mutations."""
    if not mutations:
        return (0, 0)
    gens = [m.generation for m in mutations]
    return (min(gens), max(gens))


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_mutations(mutations: list[Mutation]) -> str:
    """Format a full mutation catalog as readable text."""
    if not mutations:
        return "No mutations detected."

    lines = []
    min_gen, max_gen = generation_span(mutations)
    total_gens = max_gen - min_gen if max_gen > min_gen else 1
    rate = mutation_rate(mutations, total_gens)

    lines.append(f"Mutation Catalog: {len(mutations)} mutations across generations {min_gen}-{max_gen}")
    lines.append(f"Rate: {rate:.2f} mutations/generation")
    lines.append("")

    beneficial = filter_by_classification(mutations, Classification.BENEFICIAL)
    harmful = filter_by_classification(mutations, Classification.HARMFUL)
    neutral = filter_by_classification(mutations, Classification.NEUTRAL)
    unknown = filter_by_classification(mutations, Classification.UNKNOWN)

    lines.append(f"  beneficial: {len(beneficial)}  harmful: {len(harmful)}  neutral: {len(neutral)}  unknown: {len(unknown)}")
    lines.append("")

    current_gen = None
    for m in mutations:
        if m.generation != current_gen:
            current_gen = m.generation
            lines.append(f"--- Generation {current_gen} ---")
        marker = {"beneficial": "+", "harmful": "-", "neutral": "~", "unknown": "?"}
        symbol = marker.get(m.classification.value, "?")
        lines.append(
            f"  [{symbol}] {m.gene_name}: {m.mutation_type.value} "
            f"(mag={m.magnitude:.2f}) -- {m.details}"
        )

    return "\n".join(lines)


def format_gene_mutations(gene_name: str, mutations: list[Mutation]) -> str:
    """Format a single gene's mutation history."""
    gene_muts = filter_by_gene(mutations, gene_name)
    if not gene_muts:
        return f"No mutations detected for gene '{gene_name}'."

    lines = [f"Mutation history for '{gene_name}': {len(gene_muts)} mutations"]
    lines.append("")

    for m in gene_muts:
        marker = {"beneficial": "+", "harmful": "-", "neutral": "~", "unknown": "?"}
        symbol = marker.get(m.classification.value, "?")
        lines.append(
            f"  Gen {m.generation} [{symbol}] {m.mutation_type.value} "
            f"(mag={m.magnitude:.2f}) -- {m.details}"
        )

    beneficial = len([m for m in gene_muts if m.classification == Classification.BENEFICIAL])
    harmful = len([m for m in gene_muts if m.classification == Classification.HARMFUL])
    lines.append("")
    lines.append(f"  Summary: {beneficial} beneficial, {harmful} harmful, {len(gene_muts) - beneficial - harmful} other")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_genome_history(path: str) -> list[dict]:
    """Load genome history from a JSONL or JSON file."""
    p = Path(path)
    if not p.exists():
        return []
    text = p.read_text().strip()
    if not text:
        return []
    # Try JSONL first
    if text.startswith("{"):
        entries = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                entries.append(json.loads(line))
        return entries
    return json.loads(text)


def _load_runs(path: str | None) -> list[dict] | None:
    """Load runs from a JSONL file."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    entries = []
    for line in p.read_text().strip().splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    args = argv if argv is not None else sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print("Usage: gene_mutations.py <command> [options]")
        print("")
        print("Commands:")
        print("  mutations [--genome FILE] [--runs FILE]       Full mutation catalog")
        print("  mutations <gene> [--genome FILE] [--runs FILE] Single gene history")
        print("  beneficial [--genome FILE] [--runs FILE]       Beneficial mutations only")
        print("  harmful [--genome FILE] [--runs FILE]          Harmful mutations only")
        print("  rate [--genome FILE] [--runs FILE]             Mutation rate")
        return

    command = args[0]

    # Parse flags
    genome_path = "data/genome-history.jsonl"
    runs_path: str | None = None
    gene_name: str | None = None
    i = 1
    while i < len(args):
        if args[i] == "--genome" and i + 1 < len(args):
            genome_path = args[i + 1]
            i += 2
        elif args[i] == "--runs" and i + 1 < len(args):
            runs_path = args[i + 1]
            i += 2
        else:
            gene_name = args[i]
            i += 1

    history = _load_genome_history(genome_path)
    runs = _load_runs(runs_path)

    if not history:
        print(f"No genome history found at {genome_path}")
        return

    mutations = detect_mutations(history, runs)

    if command == "mutations":
        if gene_name:
            print(format_gene_mutations(gene_name, mutations))
        else:
            print(format_mutations(mutations))
    elif command == "beneficial":
        beneficial = filter_by_classification(mutations, Classification.BENEFICIAL)
        if gene_name:
            beneficial = filter_by_gene(beneficial, gene_name)
        print(format_mutations(beneficial))
    elif command == "harmful":
        harmful = filter_by_classification(mutations, Classification.HARMFUL)
        if gene_name:
            harmful = filter_by_gene(harmful, gene_name)
        print(format_mutations(harmful))
    elif command == "rate":
        min_gen, max_gen = generation_span(mutations)
        total_gens = max_gen - min_gen if max_gen > min_gen else 1
        rate = mutation_rate(mutations, total_gens)
        print(f"Mutation rate: {rate:.2f} mutations/generation ({len(mutations)} mutations across {total_gens} generations)")
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
