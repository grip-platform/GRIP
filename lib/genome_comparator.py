#!/usr/bin/env python3
"""GRIP Genome Comparator -- side-by-side genome diff.

Compare two genome snapshots with diff highlighting. Tracks 7 change types:
added, removed, improved, degraded, stable, activated, deactivated.

Complements genome_diff.py (which handles history-based timeline diffs)
by providing richer side-by-side comparison with tier migration tracking
and dormancy/activation detection.

Usage:
  python3 lib/genome_comparator.py 20 26           # compare gen 20 vs 26
  python3 lib/genome_comparator.py --file a.json b.json
  python3 lib/genome_comparator.py 20 26 --json
  python3 lib/genome_comparator.py 20 26 --detailed
  python3 lib/genome_comparator.py 20 26 --migration

Primary Author: V>>
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

# Tier thresholds (aligned with gene_lifecycle.py)
TIER_ESSENTIAL = 0.8
TIER_ACTIVE = 0.5
TIER_DORMANT = 0.3
TIER_DEAD = 0.1


def classify_tier(fitness: float) -> str:
    """Classify gene tier from fitness value."""
    if fitness >= TIER_ESSENTIAL:
        return "essential"
    if fitness >= TIER_ACTIVE:
        return "active"
    if fitness >= TIER_DEAD:
        return "dormant"
    return "dead"


# =========================================================================
# Data structures
# =========================================================================


class GeneStatus(Enum):
    ADDED = "added"
    REMOVED = "removed"
    IMPROVED = "improved"
    DEGRADED = "degraded"
    STABLE = "stable"
    ACTIVATED = "activated"      # dormant -> expressed
    DEACTIVATED = "deactivated"  # expressed -> dormant


@dataclass
class GeneState:
    name: str
    fitness: float
    invocations: int = 0
    is_expressed: Optional[bool] = None
    is_dormant: Optional[bool] = None
    tier: str = ""

    def __post_init__(self):
        if not self.tier:
            self.tier = classify_tier(self.fitness)
        # Derive dormancy from fitness when not explicitly set
        if self.is_dormant is None:
            self.is_dormant = self.fitness < TIER_DORMANT
        if self.is_expressed is None:
            self.is_expressed = not self.is_dormant


@dataclass
class GenomeSnapshot:
    generation: int
    genes: List[GeneState]
    total_genes: int
    expressed_count: int
    dormant_count: int
    fitness_mean: float
    fitness_median: float
    fitness_stdev: float
    source: str = ""  # file path or "gen:N"


@dataclass
class GeneDiff:
    name: str
    status: GeneStatus
    old_fitness: Optional[float] = None
    new_fitness: Optional[float] = None
    fitness_delta: float = 0.0
    old_tier: str = ""
    new_tier: str = ""


@dataclass
class ComparisonResult:
    snapshot_a: GenomeSnapshot
    snapshot_b: GenomeSnapshot
    added: List[GeneDiff] = field(default_factory=list)
    removed: List[GeneDiff] = field(default_factory=list)
    improved: List[GeneDiff] = field(default_factory=list)
    degraded: List[GeneDiff] = field(default_factory=list)
    stable: List[GeneDiff] = field(default_factory=list)
    activated: List[GeneDiff] = field(default_factory=list)
    deactivated: List[GeneDiff] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


# =========================================================================
# Snapshot building
# =========================================================================


def _extract_gene_state(name: str, gene_data: dict) -> GeneState:
    """Extract GeneState from a genome gene dict."""
    fitness = float(gene_data.get("fitness", 0.0))
    invocations = int(gene_data.get("invocation_count", 0))
    is_dormant = fitness < TIER_DORMANT
    return GeneState(
        name=name,
        fitness=fitness,
        invocations=invocations,
        is_expressed=not is_dormant,
        is_dormant=is_dormant,
    )


def build_snapshot(genome_data: dict, source: str = "") -> GenomeSnapshot:
    """Convert raw genome dict into GenomeSnapshot with stats.

    Args:
        genome_data: Full genome dict with 'genes' key containing gene objects.
        source: Label for the snapshot source (file path, "gen:N", etc).

    Returns:
        GenomeSnapshot with computed statistics.
    """
    generation = genome_data.get("generation", 0)
    raw_genes = genome_data.get("genes", {})

    genes: List[GeneState] = []
    for name, gdata in raw_genes.items():
        if isinstance(gdata, dict):
            genes.append(_extract_gene_state(name, gdata))
        else:
            # Handle case where gene data is just a fitness float
            fitness = float(gdata)
            genes.append(GeneState(
                name=name,
                fitness=fitness,
                is_dormant=fitness < TIER_DORMANT,
                is_expressed=fitness >= TIER_DORMANT,
            ))

    fitnesses = [g.fitness for g in genes]
    expressed = [g for g in genes if g.is_expressed]
    dormant = [g for g in genes if g.is_dormant]

    return GenomeSnapshot(
        generation=generation,
        genes=genes,
        total_genes=len(genes),
        expressed_count=len(expressed),
        dormant_count=len(dormant),
        fitness_mean=statistics.mean(fitnesses) if fitnesses else 0.0,
        fitness_median=statistics.median(fitnesses) if fitnesses else 0.0,
        fitness_stdev=statistics.stdev(fitnesses) if len(fitnesses) >= 2 else 0.0,
        source=source,
    )


def load_snapshot_from_file(path: str) -> GenomeSnapshot:
    """Load genome JSON and build snapshot with computed stats.

    Args:
        path: Path to a genome JSON file.

    Returns:
        GenomeSnapshot built from the file data.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    p = Path(path)
    data = json.loads(p.read_text())
    return build_snapshot(data, source=str(p))


def load_snapshot_from_generation(
    gen_number: int,
    data_dir: Optional[str] = None,
) -> GenomeSnapshot:
    """Look for genome backup by generation number.

    Searches in data/ and cache/ directories for genome files that match
    the requested generation.

    Args:
        gen_number: Generation number to find.
        data_dir: Override base directory (defaults to CLAUDE_DIR).

    Returns:
        GenomeSnapshot for the requested generation.

    Raises:
        FileNotFoundError: If no genome file for the generation is found.
    """
    base = Path(data_dir) if data_dir else CLAUDE_DIR

    # Check current genome.json first
    genome_path = base / "cache" / "genome.json"
    if genome_path.exists():
        try:
            data = json.loads(genome_path.read_text())
            if data.get("generation") == gen_number:
                return build_snapshot(data, source=f"gen:{gen_number}")
        except (json.JSONDecodeError, OSError):
            pass

    # Check genome-history.jsonl for summary snapshots
    history_path = base / "cache" / "genome-history.jsonl"
    if history_path.exists():
        try:
            for line in history_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("generation") == gen_number:
                        # History entries may have 'genes' or just summary data
                        return build_snapshot(entry, source=f"gen:{gen_number}")
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass

    # Check backup directories
    for search_dir in ["data", "cache"]:
        d = base / search_dir
        if not d.exists():
            continue
        for candidate in sorted(d.iterdir()):
            if not candidate.suffix == ".json":
                continue
            if f"gen-{gen_number}" in candidate.name or f"gen{gen_number}" in candidate.name:
                try:
                    data = json.loads(candidate.read_text())
                    if data.get("generation") == gen_number:
                        return build_snapshot(data, source=str(candidate))
                except (json.JSONDecodeError, OSError):
                    continue

    raise FileNotFoundError(
        f"No genome snapshot found for generation {gen_number}"
    )


# =========================================================================
# Comparison engine
# =========================================================================


def compare_genomes(
    a: GenomeSnapshot,
    b: GenomeSnapshot,
    threshold: float = 0.05,
) -> ComparisonResult:
    """Full comparison of two genome snapshots.

    Args:
        a: First (older) snapshot.
        b: Second (newer) snapshot.
        threshold: Minimum fitness delta to classify as improved/degraded.

    Returns:
        ComparisonResult with all change categories populated.
    """
    result = ComparisonResult(snapshot_a=a, snapshot_b=b)

    genes_a: Dict[str, GeneState] = {g.name: g for g in a.genes}
    genes_b: Dict[str, GeneState] = {g.name: g for g in b.genes}

    names_a: Set[str] = set(genes_a.keys())
    names_b: Set[str] = set(genes_b.keys())

    # Added genes (in b but not a)
    for name in sorted(names_b - names_a):
        gb = genes_b[name]
        result.added.append(GeneDiff(
            name=name,
            status=GeneStatus.ADDED,
            new_fitness=gb.fitness,
            new_tier=gb.tier,
        ))

    # Removed genes (in a but not b)
    for name in sorted(names_a - names_b):
        ga = genes_a[name]
        result.removed.append(GeneDiff(
            name=name,
            status=GeneStatus.REMOVED,
            old_fitness=ga.fitness,
            old_tier=ga.tier,
        ))

    # Genes present in both
    for name in sorted(names_a & names_b):
        ga = genes_a[name]
        gb = genes_b[name]
        delta = gb.fitness - ga.fitness

        diff = GeneDiff(
            name=name,
            status=GeneStatus.STABLE,  # overwritten below
            old_fitness=ga.fitness,
            new_fitness=gb.fitness,
            fitness_delta=delta,
            old_tier=ga.tier,
            new_tier=gb.tier,
        )

        # Check activation/deactivation first (takes priority)
        if ga.is_dormant and gb.is_expressed:
            diff.status = GeneStatus.ACTIVATED
            result.activated.append(diff)
        elif ga.is_expressed and gb.is_dormant:
            diff.status = GeneStatus.DEACTIVATED
            result.deactivated.append(diff)
        elif delta > threshold:
            diff.status = GeneStatus.IMPROVED
            result.improved.append(diff)
        elif delta < -threshold:
            diff.status = GeneStatus.DEGRADED
            result.degraded.append(diff)
        else:
            diff.status = GeneStatus.STABLE
            result.stable.append(diff)

    result.summary = compute_summary(result)
    return result


def compute_summary(result: ComparisonResult) -> Dict[str, Any]:
    """Compute summary statistics for a comparison result.

    Args:
        result: ComparisonResult to summarize.

    Returns:
        Dictionary with summary statistics.
    """
    a = result.snapshot_a
    b = result.snapshot_b

    total_changes = (
        len(result.added) + len(result.removed) +
        len(result.improved) + len(result.degraded) +
        len(result.activated) + len(result.deactivated)
    )

    # Net fitness delta
    fitness_delta = b.fitness_mean - a.fitness_mean

    # Gene count delta
    gene_delta = b.total_genes - a.total_genes

    # Diversity change (stdev as proxy)
    diversity_delta = b.fitness_stdev - a.fitness_stdev

    # Tier migration counts
    tier_migrations: Dict[str, int] = {}
    for diff_list in [result.improved, result.degraded,
                      result.activated, result.deactivated]:
        for d in diff_list:
            if d.old_tier and d.new_tier and d.old_tier != d.new_tier:
                key = f"{d.old_tier} -> {d.new_tier}"
                tier_migrations[key] = tier_migrations.get(key, 0) + 1

    return {
        "total_changes": total_changes,
        "net_fitness_delta": round(fitness_delta, 6),
        "gene_count_delta": gene_delta,
        "diversity_delta": round(diversity_delta, 6),
        "tier_migrations": tier_migrations,
        "added_count": len(result.added),
        "removed_count": len(result.removed),
        "improved_count": len(result.improved),
        "degraded_count": len(result.degraded),
        "stable_count": len(result.stable),
        "activated_count": len(result.activated),
        "deactivated_count": len(result.deactivated),
    }


# =========================================================================
# Formatting
# =========================================================================


def _sign(value: float) -> str:
    """Format a number with a sign prefix."""
    if value > 0:
        return f"+{value}"
    return str(value)


def _sign_f(value: float, decimals: int = 2) -> str:
    """Format a float with a sign prefix."""
    if value > 0:
        return f"+{value:.{decimals}f}"
    return f"{value:.{decimals}f}"


def format_comparison(result: ComparisonResult, detailed: bool = False) -> str:
    """Format comparison as ASCII side-by-side display.

    Args:
        result: ComparisonResult to format.
        detailed: If True, include stable genes in output.

    Returns:
        Formatted ASCII string.
    """
    a = result.snapshot_a
    b = result.snapshot_b
    s = result.summary

    lines: List[str] = []

    # Header
    src_a = a.source or f"Gen {a.generation}"
    src_b = b.source or f"Gen {b.generation}"
    lines.append(f"Genome Comparison: {src_a} vs {src_b}")
    lines.append("=" * 60)
    lines.append("")

    # Side-by-side stats
    col_a = f"Gen {a.generation}"
    col_b = f"Gen {b.generation}"
    lines.append(f"{'':15s} {col_a:>12s} {col_b:>12s} {'Delta':>12s}")
    lines.append(f"{'':15s} {'------':>12s} {'------':>12s} {'-----':>12s}")
    lines.append(
        f"{'Genes:':15s} {a.total_genes:>12d} {b.total_genes:>12d} "
        f"{_sign(b.total_genes - a.total_genes):>12s}"
    )
    lines.append(
        f"{'Expressed:':15s} {a.expressed_count:>12d} {b.expressed_count:>12d} "
        f"{_sign(b.expressed_count - a.expressed_count):>12s}"
    )
    lines.append(
        f"{'Dormant:':15s} {a.dormant_count:>12d} {b.dormant_count:>12d} "
        f"{_sign(b.dormant_count - a.dormant_count):>12s}"
    )
    lines.append(
        f"{'Mean Fit:':15s} {a.fitness_mean:>12.4f} {b.fitness_mean:>12.4f} "
        f"{_sign_f(b.fitness_mean - a.fitness_mean, 4):>12s}"
    )
    lines.append(
        f"{'Median:':15s} {a.fitness_median:>12.4f} {b.fitness_median:>12.4f} "
        f"{_sign_f(b.fitness_median - a.fitness_median, 4):>12s}"
    )
    lines.append(
        f"{'Stdev:':15s} {a.fitness_stdev:>12.4f} {b.fitness_stdev:>12.4f} "
        f"{_sign_f(b.fitness_stdev - a.fitness_stdev, 4):>12s}"
    )
    lines.append("")

    # Changes
    total = s.get("total_changes", 0)
    lines.append(f"Changes ({total}):")

    # Added
    for d in result.added:
        fit = f"{d.new_fitness:.2f}" if d.new_fitness is not None else "?"
        lines.append(f"  + {d.name:<30s} {'---':>6s} -> {fit:>6s}   NEW")

    # Improved
    for d in sorted(result.improved, key=lambda x: x.fitness_delta, reverse=True):
        old = f"{d.old_fitness:.2f}" if d.old_fitness is not None else "?"
        new = f"{d.new_fitness:.2f}" if d.new_fitness is not None else "?"
        delta = _sign_f(d.fitness_delta, 2)
        lines.append(f"  ^ {d.name:<30s} {old:>6s} -> {new:>6s}   {delta}")

    # Degraded
    for d in sorted(result.degraded, key=lambda x: x.fitness_delta):
        old = f"{d.old_fitness:.2f}" if d.old_fitness is not None else "?"
        new = f"{d.new_fitness:.2f}" if d.new_fitness is not None else "?"
        delta = _sign_f(d.fitness_delta, 2)
        lines.append(f"  v {d.name:<30s} {old:>6s} -> {new:>6s}   {delta}")

    # Removed
    for d in result.removed:
        fit = f"{d.old_fitness:.2f}" if d.old_fitness is not None else "?"
        lines.append(f"  - {d.name:<30s} {fit:>6s} -> {'---':>6s}   REMOVED")

    # Activated
    for d in result.activated:
        old = f"{d.old_fitness:.2f}" if d.old_fitness is not None else "?"
        new = f"{d.new_fitness:.2f}" if d.new_fitness is not None else "?"
        lines.append(f"  * {d.name:<30s} {old:>6s} -> {new:>6s}   dormant -> expressed")

    # Deactivated
    for d in result.deactivated:
        old = f"{d.old_fitness:.2f}" if d.old_fitness is not None else "?"
        new = f"{d.new_fitness:.2f}" if d.new_fitness is not None else "?"
        lines.append(f"  o {d.name:<30s} {old:>6s} -> {new:>6s}   expressed -> dormant")

    # Stable (only in detailed mode)
    if detailed and result.stable:
        lines.append("")
        lines.append(f"Stable ({len(result.stable)}):")
        for d in sorted(result.stable, key=lambda x: x.new_fitness or 0, reverse=True):
            fit = f"{d.new_fitness:.2f}" if d.new_fitness is not None else "?"
            lines.append(f"  = {d.name:<30s} {fit:>6s}            STABLE")

    if total == 0 and not detailed:
        lines.append("  (no changes)")

    lines.append("")
    return "\n".join(lines)


def format_json(result: ComparisonResult) -> str:
    """Format comparison as JSON.

    Args:
        result: ComparisonResult to serialize.

    Returns:
        JSON string.
    """
    def _snapshot_dict(snap: GenomeSnapshot) -> dict:
        return {
            "generation": snap.generation,
            "total_genes": snap.total_genes,
            "expressed_count": snap.expressed_count,
            "dormant_count": snap.dormant_count,
            "fitness_mean": round(snap.fitness_mean, 6),
            "fitness_median": round(snap.fitness_median, 6),
            "fitness_stdev": round(snap.fitness_stdev, 6),
            "source": snap.source,
        }

    def _diff_dict(d: GeneDiff) -> dict:
        return {
            "name": d.name,
            "status": d.status.value,
            "old_fitness": round(d.old_fitness, 6) if d.old_fitness is not None else None,
            "new_fitness": round(d.new_fitness, 6) if d.new_fitness is not None else None,
            "fitness_delta": round(d.fitness_delta, 6),
            "old_tier": d.old_tier,
            "new_tier": d.new_tier,
        }

    output = {
        "snapshot_a": _snapshot_dict(result.snapshot_a),
        "snapshot_b": _snapshot_dict(result.snapshot_b),
        "added": [_diff_dict(d) for d in result.added],
        "removed": [_diff_dict(d) for d in result.removed],
        "improved": [_diff_dict(d) for d in result.improved],
        "degraded": [_diff_dict(d) for d in result.degraded],
        "stable": [_diff_dict(d) for d in result.stable],
        "activated": [_diff_dict(d) for d in result.activated],
        "deactivated": [_diff_dict(d) for d in result.deactivated],
        "summary": result.summary,
    }

    return json.dumps(output, indent=2)


def format_migration_report(result: ComparisonResult) -> str:
    """Show tier movements between snapshots.

    Args:
        result: ComparisonResult to analyze.

    Returns:
        Formatted ASCII migration report.
    """
    a = result.snapshot_a
    b = result.snapshot_b

    lines: List[str] = []
    lines.append(f"Tier Migration Report: Gen {a.generation} -> Gen {b.generation}")
    lines.append("=" * 60)
    lines.append("")

    # Collect all tier changes
    migrations: List[Tuple[str, str, str, float, float]] = []
    for diff_list in [result.improved, result.degraded,
                      result.activated, result.deactivated,
                      result.added, result.removed]:
        for d in diff_list:
            old_t = d.old_tier or "(none)"
            new_t = d.new_tier or "(none)"
            if old_t != new_t:
                migrations.append((
                    d.name,
                    old_t,
                    new_t,
                    d.old_fitness if d.old_fitness is not None else 0.0,
                    d.new_fitness if d.new_fitness is not None else 0.0,
                ))

    if not migrations:
        lines.append("  No tier migrations detected.")
        lines.append("")
        return "\n".join(lines)

    # Group by migration path
    paths: Dict[str, List[Tuple[str, float, float]]] = {}
    for name, old_t, new_t, old_f, new_f in migrations:
        key = f"{old_t} -> {new_t}"
        paths.setdefault(key, []).append((name, old_f, new_f))

    # Promotion order (positive first)
    tier_rank = {"dead": 0, "(none)": 0, "dormant": 1, "active": 2, "essential": 3}

    def _is_promotion(path_key: str) -> bool:
        parts = path_key.split(" -> ")
        if len(parts) != 2:
            return False
        return tier_rank.get(parts[1], 0) > tier_rank.get(parts[0], 0)

    promoted = {k: v for k, v in paths.items() if _is_promotion(k)}
    demoted = {k: v for k, v in paths.items() if not _is_promotion(k)}

    if promoted:
        lines.append("Promotions:")
        for path_key in sorted(promoted.keys()):
            genes = promoted[path_key]
            lines.append(f"  {path_key} ({len(genes)}):")
            for name, old_f, new_f in sorted(genes, key=lambda x: x[2], reverse=True):
                lines.append(f"    {name:<35s} {old_f:.2f} -> {new_f:.2f}")
        lines.append("")

    if demoted:
        lines.append("Demotions:")
        for path_key in sorted(demoted.keys()):
            genes = demoted[path_key]
            lines.append(f"  {path_key} ({len(genes)}):")
            for name, old_f, new_f in sorted(genes, key=lambda x: x[2]):
                lines.append(f"    {name:<35s} {old_f:.2f} -> {new_f:.2f}")
        lines.append("")

    # Summary
    total_promoted = sum(len(v) for v in promoted.values())
    total_demoted = sum(len(v) for v in demoted.values())
    lines.append(
        f"Total: {len(migrations)} migrations "
        f"({total_promoted} promoted, {total_demoted} demoted)"
    )
    lines.append("")

    return "\n".join(lines)


# =========================================================================
# CLI
# =========================================================================


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="GRIP Genome Comparator -- side-by-side genome diff",
        prog="genome_comparator",
    )
    parser.add_argument(
        "gen_a", nargs="?", type=int, default=None,
        help="First generation number",
    )
    parser.add_argument(
        "gen_b", nargs="?", type=int, default=None,
        help="Second generation number",
    )
    parser.add_argument(
        "--file", nargs=2, metavar=("FILE_A", "FILE_B"),
        help="Two file paths to compare",
    )
    parser.add_argument(
        "--detailed", action="store_true",
        help="Include stable genes in output",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output in JSON format",
    )
    parser.add_argument(
        "--migration", action="store_true",
        help="Show tier migration report",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.05,
        help="Fitness delta threshold for improved/degraded (default: 0.05)",
    )
    parser.add_argument(
        "--claude-dir", type=str, default=None,
        help="Override CLAUDE_DIR (for testing)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    base_dir = args.claude_dir if args.claude_dir else str(CLAUDE_DIR)

    try:
        if args.file:
            snap_a = load_snapshot_from_file(args.file[0])
            snap_b = load_snapshot_from_file(args.file[1])
        elif args.gen_a is not None and args.gen_b is not None:
            snap_a = load_snapshot_from_generation(args.gen_a, data_dir=base_dir)
            snap_b = load_snapshot_from_generation(args.gen_b, data_dir=base_dir)
        elif args.gen_a is not None:
            # Compare gen_a against current
            snap_a = load_snapshot_from_generation(args.gen_a, data_dir=base_dir)
            genome_path = Path(base_dir) / "cache" / "genome.json"
            if not genome_path.exists():
                print("Error: no current genome.json found for comparison target")
                return 1
            snap_b = load_snapshot_from_file(str(genome_path))
        else:
            parser.print_help()
            return 0
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON -- {e}")
        return 1

    result = compare_genomes(snap_a, snap_b, threshold=args.threshold)

    if args.json:
        print(format_json(result))
    elif args.migration:
        print(format_migration_report(result))
    else:
        print(format_comparison(result, detailed=args.detailed))

    return 0


if __name__ == "__main__":
    sys.exit(main())
