#!/usr/bin/env python3
"""
genome_diff.py -- Cross-session genome diff visualization.

Compare genome states between generations and visualize gene evolution.
Data sources:
  - cache/genome.json: current genome state (full gene data)
  - cache/genome-history.jsonl: per-save snapshots (summary metrics)

Usage:
  python3 lib/genome_diff.py diff [gen_a] [gen_b]   # compare two generations
  python3 lib/genome_diff.py timeline                # fitness trajectory
  python3 lib/genome_diff.py stable                  # gene stability report
  python3 lib/genome_diff.py diff --json             # machine-readable output

Primary Author: V>>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
GENOME_PATH = CLAUDE_DIR / "cache" / "genome.json"
GENOME_HISTORY_PATH = CLAUDE_DIR / "cache" / "genome-history.jsonl"


# =========================================================================
# Data loading
# =========================================================================

def load_genome_snapshots(
    history_path: Optional[Path] = None,
    genome_path: Optional[Path] = None,
) -> list[dict]:
    """Load all genome snapshots sorted by generation.

    Sources:
      1. genome-history.jsonl -- one JSON object per line (summary snapshots)
      2. genome.json -- full current genome (used for gene-level diffs)

    Returns deduplicated snapshots sorted by (generation, timestamp).
    Each snapshot has at minimum: generation, id, avg_fitness, gene_count.
    """
    hp = history_path or GENOME_HISTORY_PATH
    gp = genome_path or GENOME_PATH

    snapshots: list[dict] = []

    # Load history file
    if hp.exists():
        try:
            for line in hp.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    snapshots.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass

    # Deduplicate: keep latest entry per (generation, id) pair
    seen: dict[tuple, int] = {}
    for i, s in enumerate(snapshots):
        key = (s.get("generation", 0), s.get("id", ""))
        seen[key] = i  # last occurrence wins

    deduped = [snapshots[i] for i in sorted(seen.values())]

    # Sort by generation, then timestamp
    deduped.sort(key=lambda s: (s.get("generation", 0), s.get("timestamp", "")))

    return deduped


def load_full_genome(genome_path: Optional[Path] = None) -> Optional[dict]:
    """Load the full genome.json with all gene data."""
    gp = genome_path or GENOME_PATH
    if not gp.exists():
        return None
    try:
        return json.loads(gp.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# =========================================================================
# Diff computation
# =========================================================================

@dataclass
class GenomeDiff:
    """Result of comparing two genome states."""
    generation_a: int
    generation_b: int
    genes_added: list[str] = field(default_factory=list)
    genes_removed: list[str] = field(default_factory=list)
    fitness_changes: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    # fitness_changes: name -> (old_fitness, new_fitness, delta)
    gene_count_a: int = 0
    gene_count_b: int = 0
    avg_fitness_a: float = 0.0
    avg_fitness_b: float = 0.0

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict."""
        return {
            "generation_a": self.generation_a,
            "generation_b": self.generation_b,
            "genes_added": self.genes_added,
            "genes_removed": self.genes_removed,
            "fitness_changes": {
                name: {"old": old, "new": new, "delta": delta}
                for name, (old, new, delta) in self.fitness_changes.items()
            },
            "gene_count_a": self.gene_count_a,
            "gene_count_b": self.gene_count_b,
            "avg_fitness_a": self.avg_fitness_a,
            "avg_fitness_b": self.avg_fitness_b,
        }


def diff_genomes(genome_a: dict, genome_b: dict) -> GenomeDiff:
    """Compute differences between two genome states.

    Accepts either full genome dicts (with 'genes' key containing gene objects)
    or summary snapshots (with 'avg_fitness', 'gene_count' etc).
    """
    gen_a = genome_a.get("generation", 0)
    gen_b = genome_b.get("generation", 0)

    diff = GenomeDiff(
        generation_a=gen_a,
        generation_b=gen_b,
        gene_count_a=genome_a.get("gene_count", len(genome_a.get("genes", {}))),
        gene_count_b=genome_b.get("gene_count", len(genome_b.get("genes", {}))),
        avg_fitness_a=genome_a.get("avg_fitness", 0.0),
        avg_fitness_b=genome_b.get("avg_fitness", 0.0),
    )

    genes_a = genome_a.get("genes", {})
    genes_b = genome_b.get("genes", {})

    if not genes_a and not genes_b:
        # Summary-only snapshots -- no gene-level diff possible
        return diff

    names_a = set(genes_a.keys())
    names_b = set(genes_b.keys())

    diff.genes_added = sorted(names_b - names_a)
    diff.genes_removed = sorted(names_a - names_b)

    # Fitness changes for genes present in both
    for name in sorted(names_a & names_b):
        old_fit = _extract_fitness(genes_a[name])
        new_fit = _extract_fitness(genes_b[name])
        delta = round(new_fit - old_fit, 6)
        if delta != 0.0:
            diff.fitness_changes[name] = (
                round(old_fit, 4),
                round(new_fit, 4),
                round(delta, 4),
            )

    return diff


def _extract_fitness(gene_data) -> float:
    """Extract fitness from a gene dict or raw float."""
    if isinstance(gene_data, dict):
        return float(gene_data.get("fitness", 0.0))
    return float(gene_data)


# =========================================================================
# Formatting
# =========================================================================

def format_diff(diff: GenomeDiff) -> str:
    """Format a GenomeDiff as an ASCII table with +/- indicators."""
    lines = []
    lines.append(f"Genome Diff: Gen {diff.generation_a} -> Gen {diff.generation_b}")
    lines.append("=" * 60)

    # Summary
    fit_delta = diff.avg_fitness_b - diff.avg_fitness_a
    sign = "+" if fit_delta >= 0 else ""
    lines.append(
        f"  Genes: {diff.gene_count_a} -> {diff.gene_count_b}  "
        f"| Avg Fitness: {diff.avg_fitness_a:.4f} -> {diff.avg_fitness_b:.4f} ({sign}{fit_delta:.4f})"
    )
    lines.append("")

    # Added genes
    if diff.genes_added:
        lines.append(f"  + Added ({len(diff.genes_added)}):")
        for name in diff.genes_added:
            lines.append(f"    + {name}")
        lines.append("")

    # Removed genes
    if diff.genes_removed:
        lines.append(f"  - Removed ({len(diff.genes_removed)}):")
        for name in diff.genes_removed:
            lines.append(f"    - {name}")
        lines.append("")

    # Fitness changes
    if diff.fitness_changes:
        lines.append(f"  ~ Fitness Changes ({len(diff.fitness_changes)}):")
        lines.append(f"    {'Gene':<40} {'Old':>7} {'New':>7} {'Delta':>8}")
        lines.append(f"    {'-'*40} {'-'*7} {'-'*7} {'-'*8}")

        # Sort by absolute delta descending
        sorted_changes = sorted(
            diff.fitness_changes.items(),
            key=lambda x: abs(x[1][2]),
            reverse=True,
        )
        for name, (old, new, delta) in sorted_changes:
            indicator = "+" if delta > 0 else "-" if delta < 0 else " "
            # Truncate long names
            display_name = name[:38] + ".." if len(name) > 40 else name
            lines.append(
                f"    {display_name:<40} {old:>7.4f} {new:>7.4f} {indicator}{abs(delta):>7.4f}"
            )
        lines.append("")

    if not diff.genes_added and not diff.genes_removed and not diff.fitness_changes:
        lines.append("  No changes detected.")
        lines.append("")

    return "\n".join(lines)


def timeline(snapshots: list[dict]) -> str:
    """Show fitness trajectory across all saved generations.

    Returns an ASCII chart of avg_fitness over generations.
    """
    if not snapshots:
        return "No genome snapshots found."

    if len(snapshots) == 1:
        s = snapshots[0]
        return (
            f"Single snapshot: Gen {s.get('generation', 0)} "
            f"| Fitness: {s.get('avg_fitness', 0.0):.4f} "
            f"| Genes: {s.get('gene_count', 0)}"
        )

    lines = []
    lines.append("Genome Fitness Timeline")
    lines.append("=" * 70)

    # Collect data points (one per unique generation)
    gen_data: dict[int, dict] = {}
    for s in snapshots:
        gen = s.get("generation", 0)
        gen_data[gen] = s  # last snapshot per generation wins

    points = sorted(gen_data.items())
    fitnesses = [s.get("avg_fitness", 0.0) for _, s in points]

    min_fit = min(fitnesses) if fitnesses else 0.0
    max_fit = max(fitnesses) if fitnesses else 1.0
    fit_range = max_fit - min_fit if max_fit > min_fit else 1.0

    chart_width = 40

    lines.append(f"  {'Gen':>4}  {'Fitness':>8}  {'Genes':>5}  {'Chart'}")
    lines.append(f"  {'-'*4}  {'-'*8}  {'-'*5}  {'-'*chart_width}")

    for gen, s in points:
        fit = s.get("avg_fitness", 0.0)
        gene_count = s.get("gene_count", 0)
        # Normalize to chart width
        bar_len = int(((fit - min_fit) / fit_range) * chart_width) if fit_range > 0 else 0
        bar_len = max(1, min(chart_width, bar_len))
        bar = "#" * bar_len
        lines.append(f"  {gen:>4}  {fit:>8.4f}  {gene_count:>5}  {bar}")

    # Summary
    lines.append("")
    first_fit = fitnesses[0]
    last_fit = fitnesses[-1]
    overall_delta = last_fit - first_fit
    sign = "+" if overall_delta >= 0 else ""
    lines.append(
        f"  Overall: {first_fit:.4f} -> {last_fit:.4f} ({sign}{overall_delta:.4f}) "
        f"across {len(points)} generations"
    )

    return "\n".join(lines)


def gene_stability(snapshots: list[dict]) -> dict[str, dict]:
    """Analyze which genes have been stable vs volatile across generations.

    Only works when snapshots contain full gene data (i.e., loaded from
    genome.json files, not summary-only history).

    Returns dict of gene_name -> {
        'appearances': int,
        'fitness_values': list[float],
        'fitness_std': float,
        'min_fitness': float,
        'max_fitness': float,
        'trend': str ('rising', 'falling', 'stable', 'volatile'),
    }
    """
    gene_tracker: dict[str, list[tuple[int, float]]] = {}

    for s in snapshots:
        gen = s.get("generation", 0)
        genes = s.get("genes", {})
        for name, gdata in genes.items():
            fit = _extract_fitness(gdata)
            gene_tracker.setdefault(name, []).append((gen, fit))

    result = {}
    for name, entries in sorted(gene_tracker.items()):
        fitnesses = [f for _, f in entries]
        n = len(fitnesses)

        if n == 0:
            continue

        mean = sum(fitnesses) / n
        variance = sum((f - mean) ** 2 for f in fitnesses) / n if n > 1 else 0.0
        std = variance ** 0.5

        min_f = min(fitnesses)
        max_f = max(fitnesses)

        # Determine trend
        if n < 2:
            trend = "stable"
        else:
            first_half = fitnesses[:n // 2] or fitnesses[:1]
            second_half = fitnesses[n // 2:] or fitnesses[-1:]
            avg_first = sum(first_half) / len(first_half)
            avg_second = sum(second_half) / len(second_half)
            delta = avg_second - avg_first

            # Volatile = high variance relative to the mean, AND not a
            # clear monotonic trend. A steady rise from 0.3->0.9 has
            # high absolute std but is clearly directional, not volatile.
            # We detect volatility by checking if direction reversals
            # dominate the signal.
            reversals = 0
            for i in range(1, n - 1):
                d1 = fitnesses[i] - fitnesses[i - 1]
                d2 = fitnesses[i + 1] - fitnesses[i]
                if d1 * d2 < 0:  # sign change
                    reversals += 1
            reversal_rate = reversals / max(1, n - 2) if n > 2 else 0.0

            if std > 0.1 and reversal_rate > 0.3:
                trend = "volatile"
            elif delta > 0.02:
                trend = "rising"
            elif delta < -0.02:
                trend = "falling"
            else:
                trend = "stable"

        result[name] = {
            "appearances": n,
            "fitness_values": [round(f, 4) for f in fitnesses],
            "fitness_mean": round(mean, 4),
            "fitness_std": round(std, 4),
            "min_fitness": round(min_f, 4),
            "max_fitness": round(max_f, 4),
            "trend": trend,
        }

    return result


def format_stability(stability: dict[str, dict]) -> str:
    """Format gene stability report as ASCII table."""
    if not stability:
        return "No gene stability data available (need full genome snapshots with gene data)."

    lines = []
    lines.append("Gene Stability Report")
    lines.append("=" * 80)

    # Categorize
    categories = {"stable": [], "rising": [], "falling": [], "volatile": []}
    for name, data in stability.items():
        categories[data["trend"]].append((name, data))

    for trend_label, trend_items in [
        ("Stable", categories["stable"]),
        ("Rising", categories["rising"]),
        ("Falling", categories["falling"]),
        ("Volatile", categories["volatile"]),
    ]:
        if not trend_items:
            continue

        # Sort by std deviation (ascending for stable, descending for volatile)
        if trend_label in ("Stable", "Rising"):
            trend_items.sort(key=lambda x: x[1]["fitness_std"])
        else:
            trend_items.sort(key=lambda x: x[1]["fitness_std"], reverse=True)

        lines.append(f"\n  {trend_label} ({len(trend_items)} genes):")
        lines.append(f"    {'Gene':<35} {'Mean':>6} {'Std':>6} {'Min':>6} {'Max':>6} {'Seen':>4}")
        lines.append(f"    {'-'*35} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*4}")

        for name, data in trend_items[:20]:  # top 20 per category
            display = name[:33] + ".." if len(name) > 35 else name
            lines.append(
                f"    {display:<35} "
                f"{data['fitness_mean']:>6.3f} "
                f"{data['fitness_std']:>6.3f} "
                f"{data['min_fitness']:>6.3f} "
                f"{data['max_fitness']:>6.3f} "
                f"{data['appearances']:>4}"
            )

        if len(trend_items) > 20:
            lines.append(f"    ... and {len(trend_items) - 20} more")

    lines.append("")
    lines.append(
        f"  Total: {len(stability)} genes tracked | "
        f"Stable: {len(categories['stable'])} | "
        f"Rising: {len(categories['rising'])} | "
        f"Falling: {len(categories['falling'])} | "
        f"Volatile: {len(categories['volatile'])}"
    )

    return "\n".join(lines)


# =========================================================================
# CLI
# =========================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-session genome diff visualization",
        prog="genome_diff",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output in JSON format"
    )
    parser.add_argument(
        "--claude-dir", type=str, default=None,
        help="Override CLAUDE_DIR (for testing)",
    )

    sub = parser.add_subparsers(dest="command")

    # diff
    diff_parser = sub.add_parser("diff", help="Compare two genome generations")
    diff_parser.add_argument(
        "gen_a", nargs="?", type=int, default=None,
        help="First generation (default: previous)",
    )
    diff_parser.add_argument(
        "gen_b", nargs="?", type=int, default=None,
        help="Second generation (default: latest)",
    )

    # timeline
    sub.add_parser("timeline", help="Show fitness trajectory over generations")

    # stable
    sub.add_parser("stable", help="Show most/least stable genes")

    return parser


def _resolve_paths(args):
    """Resolve genome paths from CLI args."""
    if args.claude_dir:
        base = Path(args.claude_dir)
    else:
        base = CLAUDE_DIR
    return base / "cache" / "genome-history.jsonl", base / "cache" / "genome.json"


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    history_path, genome_path = _resolve_paths(args)

    if args.command == "diff":
        return _cmd_diff(args, history_path, genome_path)
    elif args.command == "timeline":
        return _cmd_timeline(args, history_path, genome_path)
    elif args.command == "stable":
        return _cmd_stable(args, history_path, genome_path)

    parser.print_help()
    return 1


def _cmd_diff(args, history_path: Path, genome_path: Path) -> int:
    """Handle diff subcommand."""
    snapshots = load_genome_snapshots(history_path, genome_path)

    if len(snapshots) == 0:
        msg = "No genome snapshots found."
        if args.json:
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return 1

    if len(snapshots) == 1:
        msg = "Only one snapshot exists. Need at least two for a diff."
        if args.json:
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return 1

    # Build generation index
    gen_index: dict[int, dict] = {}
    for s in snapshots:
        gen = s.get("generation", 0)
        gen_index[gen] = s

    available_gens = sorted(gen_index.keys())

    # Resolve gen_a and gen_b
    gen_a_num = args.gen_a
    gen_b_num = args.gen_b

    if gen_a_num is None and gen_b_num is None:
        # Default: previous vs latest
        if len(available_gens) >= 2:
            gen_a_num = available_gens[-2]
            gen_b_num = available_gens[-1]
        else:
            gen_a_num = available_gens[0]
            gen_b_num = available_gens[0]
    elif gen_b_num is None:
        gen_b_num = available_gens[-1]

    if gen_a_num not in gen_index:
        msg = f"Generation {gen_a_num} not found. Available: {available_gens}"
        if args.json:
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return 1

    if gen_b_num not in gen_index:
        msg = f"Generation {gen_b_num} not found. Available: {available_gens}"
        if args.json:
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return 1

    # If we have the full genome and one of the targets is the latest,
    # try loading full gene data for richer diffs
    genome_a = gen_index[gen_a_num]
    genome_b = gen_index[gen_b_num]

    # Enrich with full genome data if available
    full = load_full_genome(genome_path)
    if full and full.get("generation") == gen_b_num:
        genome_b = full
    elif full and full.get("generation") == gen_a_num:
        genome_a = full

    diff = diff_genomes(genome_a, genome_b)

    if args.json:
        print(json.dumps(diff.to_dict(), indent=2))
    else:
        print(format_diff(diff))

    return 0


def _cmd_timeline(args, history_path: Path, genome_path: Path) -> int:
    """Handle timeline subcommand."""
    snapshots = load_genome_snapshots(history_path, genome_path)

    if args.json:
        gen_data: dict[int, dict] = {}
        for s in snapshots:
            gen = s.get("generation", 0)
            gen_data[gen] = s
        points = [
            {
                "generation": gen,
                "avg_fitness": s.get("avg_fitness", 0.0),
                "gene_count": s.get("gene_count", 0),
                "timestamp": s.get("timestamp", ""),
            }
            for gen, s in sorted(gen_data.items())
        ]
        print(json.dumps({"timeline": points}, indent=2))
    else:
        print(timeline(snapshots))

    return 0


def _cmd_stable(args, history_path: Path, genome_path: Path) -> int:
    """Handle stable subcommand."""
    # Gene stability needs full gene data -- try loading from genome.json
    full = load_full_genome(genome_path)
    if not full or "genes" not in full:
        msg = "No full genome data available for stability analysis."
        if args.json:
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return 1

    # Use only the current genome (single point) -- stability across
    # history would require full gene data per snapshot, which genome-history.jsonl
    # does not contain. With a single genome, we report current state.
    stability = gene_stability([full])

    if args.json:
        print(json.dumps({"stability": stability}, indent=2))
    else:
        print(format_stability(stability))

    return 0


if __name__ == "__main__":
    sys.exit(main())
