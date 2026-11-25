#!/usr/bin/env python3
"""
genome_lineage.py -- Genome lineage tracker for GRIP's evolutionary DNA system.

Builds parent-child gene trees from genome data, visualises lineage as ASCII
trees, and diffs between generations.

Usage:
    python3 lib/genome_lineage.py tree      # ASCII lineage tree
    python3 lib/genome_lineage.py table     # Tabular lineage summary
    python3 lib/genome_lineage.py diff      # Last generation diff
    python3 lib/genome_lineage.py diff 5 10 # Diff between gen 5 and gen 10
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
GENOME_PATH = CLAUDE_DIR / "cache" / "genome.json"
GENOME_HISTORY_PATH = CLAUDE_DIR / "cache" / "genome-history.jsonl"

# Dormancy threshold: days since last invocation to consider a gene dormant
DORMANCY_DAYS = 14


# =========================================================================
# Data Structures
# =========================================================================


@dataclass
class GeneLineage:
    """Lineage record for a single gene."""

    name: str
    parent: Optional[str] = None
    created_generation: int = 0
    fitness_history: list[float] = field(default_factory=list)
    current_fitness: float = 0.0
    status: str = "active"  # active, dormant, extinct
    component_type: str = "skill"
    children: list[str] = field(default_factory=list)
    invocation_count: int = 0
    last_invoked: str = ""
    contribution_successes: int = 0
    contribution_total: int = 0


# =========================================================================
# Lineage Builder
# =========================================================================


def load_genome(path: Optional[Path] = None) -> dict:
    """Load genome from JSON. Returns empty dict on missing/corrupt file."""
    genome_path = path or GENOME_PATH
    if not genome_path.exists():
        return {}
    try:
        with open(genome_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _infer_parent(gene_name: str, gene_data: dict, all_gene_names: set[str]) -> Optional[str]:
    """Infer the parent gene from lineage field or naming convention.

    Parentage sources (in priority order):
    1. Explicit `lineage` field pointing to another gene name
    2. Lineage field with `genesis:` prefix (origin record, no parent gene)
    3. Naming convention: e.g. "skill-X" where "X" is an existing gene
    """
    lineage = gene_data.get("lineage")

    if lineage and isinstance(lineage, str):
        # genesis: prefix means externally created, no parent gene
        if lineage.startswith("genesis:"):
            return None
        # Direct gene reference
        if lineage in all_gene_names:
            return lineage

    # Naming convention: "child-parent" or "parent-variant"
    # Only match if both parts exist as genes
    parts = gene_name.rsplit("-", 1)
    if len(parts) == 2:
        prefix, suffix = parts
        if prefix in all_gene_names and prefix != gene_name:
            return prefix

    return None


def _determine_status(
    gene_data: dict, current_generation: int
) -> str:
    """Determine gene status: active, dormant, or extinct.

    - extinct: fitness below dead threshold (0.2)
    - dormant: no invocations in DORMANCY_DAYS or zero invocations and old
    - active: everything else
    """
    fitness = gene_data.get("fitness", 0.5)

    if fitness < 0.2:
        return "extinct"

    last_invoked = gene_data.get("last_invoked", "")
    invocation_count = gene_data.get("invocation_count", 0)
    generation_born = gene_data.get("generation_born", 0)
    age = current_generation - generation_born

    if last_invoked:
        try:
            last_dt = datetime.fromisoformat(last_invoked)
            now = datetime.now(timezone.utc)
            days_since = (now - last_dt).days
            if days_since > DORMANCY_DAYS:
                return "dormant"
        except (ValueError, TypeError):
            pass
    elif invocation_count == 0 and age > 5:
        return "dormant"

    return "active"


def _extract_fitness_trajectory(gene_data: dict) -> list[float]:
    """Build a fitness trajectory from the contribution record.

    Each contribution is [outcome_id, success_bool]. We build a running
    fitness estimate from the sequence of successes/failures.
    """
    record = gene_data.get("contribution_record", [])
    if not record:
        return [gene_data.get("fitness", 0.5)]

    trajectory = []
    running = 0.5
    for entry in record:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            success = entry[1]
            if success:
                running = min(1.0, running + 0.05)
            else:
                running = max(0.0, running - 0.02)
            trajectory.append(round(running, 4))

    if not trajectory:
        trajectory = [gene_data.get("fitness", 0.5)]

    return trajectory


def build_lineage(genome: dict) -> list[GeneLineage]:
    """Extract lineage records from genome data.

    Args:
        genome: Raw genome dict (as loaded from genome.json).

    Returns:
        List of GeneLineage records with parent-child relationships resolved.
    """
    genes = genome.get("genes", {})
    current_generation = genome.get("generation", 0)

    if not genes:
        return []

    all_names = set(genes.keys())
    lineage_map: dict[str, GeneLineage] = {}

    # First pass: create all lineage records
    for name, data in genes.items():
        parent = _infer_parent(name, data, all_names)
        fitness_trajectory = _extract_fitness_trajectory(data)
        contribution_record = data.get("contribution_record", [])
        successes = sum(1 for r in contribution_record if isinstance(r, (list, tuple)) and len(r) >= 2 and r[1])

        lineage_map[name] = GeneLineage(
            name=name,
            parent=parent,
            created_generation=data.get("generation_born", 0),
            fitness_history=fitness_trajectory,
            current_fitness=data.get("fitness", 0.5),
            status=_determine_status(data, current_generation),
            component_type=data.get("component_type", "skill"),
            invocation_count=data.get("invocation_count", 0),
            last_invoked=data.get("last_invoked", ""),
            contribution_successes=successes,
            contribution_total=len(contribution_record),
        )

    # Second pass: populate children lists
    for name, record in lineage_map.items():
        if record.parent and record.parent in lineage_map:
            lineage_map[record.parent].children.append(name)

    return sorted(lineage_map.values(), key=lambda g: (g.created_generation, g.name))


# =========================================================================
# Fitness Trend
# =========================================================================


def fitness_trend(history: list[float]) -> str:
    """Compute a simple trend indicator from fitness history.

    Returns one of: 'up', 'down', 'flat', 'new'
    """
    if len(history) < 2:
        return "new"

    # Compare last third vs first third
    third = max(1, len(history) // 3)
    early = sum(history[:third]) / third
    late = sum(history[-third:]) / third
    delta = late - early

    if delta > 0.02:
        return "up"
    elif delta < -0.02:
        return "down"
    return "flat"


TREND_SYMBOLS = {
    "up": "[+]",
    "down": "[-]",
    "flat": "[=]",
    "new": "[*]",
}

STATUS_SYMBOLS = {
    "active": "",
    "dormant": " (dormant)",
    "extinct": " (extinct)",
}


# =========================================================================
# ASCII Tree
# =========================================================================


def format_lineage_tree(lineage: list[GeneLineage]) -> str:
    """Render lineage as an ASCII tree.

    Root genes (no parent) at top level, children indented below.
    Each gene shows fitness, trend, and status.

    Example:
        design-principles  0.82 [+]
        |-- optimizing-complexity  0.45 [=] (dormant)
        |-- principles-criterion  0.91 [+]
        red-teaming-strategic-decisions  0.17 [-] (extinct)
    """
    if not lineage:
        return "(empty genome)"

    # Build lookup
    by_name: dict[str, GeneLineage] = {g.name: g for g in lineage}

    # Find roots (no parent, or parent not in genome)
    roots = [g for g in lineage if g.parent is None or g.parent not in by_name]

    lines: list[str] = []

    def _render_node(node: GeneLineage, prefix: str, is_last: bool, is_root: bool) -> None:
        trend = TREND_SYMBOLS.get(fitness_trend(node.fitness_history), "")
        status = STATUS_SYMBOLS.get(node.status, "")
        fitness_str = f"{node.current_fitness:.2f}"

        if is_root:
            connector = ""
        else:
            connector = "`-- " if is_last else "|-- "

        line = f"{prefix}{connector}{node.name}  {fitness_str} {trend}{status}"
        lines.append(line)

        children = sorted(node.children)
        for i, child_name in enumerate(children):
            child = by_name.get(child_name)
            if child is None:
                continue
            child_is_last = i == len(children) - 1
            if is_root:
                child_prefix = prefix
            else:
                child_prefix = prefix + ("    " if is_last else "|   ")
            _render_node(child, child_prefix, child_is_last, False)

    # Sort roots by component type then name for stable output
    roots.sort(key=lambda g: (g.component_type, g.name))

    for root in roots:
        _render_node(root, "", True, True)

    return "\n".join(lines)


# =========================================================================
# Tabular Format
# =========================================================================


def format_lineage_table(lineage: list[GeneLineage]) -> list[dict]:
    """Convert lineage to a list of dicts suitable for tabular display / MCP Apps.

    Each dict contains: name, parent, generation, fitness, trend, status, type,
    invocations, children_count.
    """
    result = []
    for g in lineage:
        result.append({
            "name": g.name,
            "parent": g.parent or "(root)",
            "generation_born": g.created_generation,
            "fitness": round(g.current_fitness, 4),
            "trend": fitness_trend(g.fitness_history),
            "status": g.status,
            "component_type": g.component_type,
            "invocations": g.invocation_count,
            "children_count": len(g.children),
            "contribution_rate": (
                round(g.contribution_successes / g.contribution_total, 2)
                if g.contribution_total > 0
                else None
            ),
        })
    return result


# =========================================================================
# Generation Diff
# =========================================================================


def _load_history_snapshots() -> list[dict]:
    """Load genome history snapshots from JSONL."""
    if not GENOME_HISTORY_PATH.exists():
        return []
    snapshots = []
    try:
        with open(GENOME_HISTORY_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        snapshots.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    return snapshots


def gene_diff(gen_a: Optional[int] = None, gen_b: Optional[int] = None, genome: Optional[dict] = None) -> dict:
    """Diff the genome between two generations.

    If gen_a/gen_b are not provided, diffs the last two generations from history.
    Uses genome-history.jsonl for snapshot metadata and the current genome
    for detailed gene-level comparison.

    Returns:
        {
            "from_generation": int,
            "to_generation": int,
            "genes_added": list[str],
            "genes_removed": list[str],
            "fitness_changes": list[dict],  # {name, old, new, delta}
            "avg_fitness_change": float,
            "generation_gap": int,
        }
    """
    genome = genome or load_genome()
    snapshots = _load_history_snapshots()

    if not snapshots:
        return {
            "from_generation": 0,
            "to_generation": genome.get("generation", 0),
            "genes_added": list(genome.get("genes", {}).keys()),
            "genes_removed": [],
            "fitness_changes": [],
            "avg_fitness_change": 0.0,
            "generation_gap": genome.get("generation", 0),
        }

    # Deduplicate snapshots by generation (keep last entry per gen)
    gen_map: dict[int, dict] = {}
    for s in snapshots:
        gen_map[s.get("generation", 0)] = s

    generations = sorted(gen_map.keys())

    if gen_a is None or gen_b is None:
        if len(generations) < 2:
            gen_a = generations[0] if generations else 0
            gen_b = genome.get("generation", 0)
        else:
            gen_a = generations[-2]
            gen_b = generations[-1]

    snap_a = gen_map.get(gen_a, {})
    snap_b = gen_map.get(gen_b, {})

    # History snapshots don't carry per-gene detail, only summaries.
    # For gene-level diff we compare gene_count and fitness changes.
    result = {
        "from_generation": gen_a,
        "to_generation": gen_b,
        "genes_added": [],
        "genes_removed": [],
        "fitness_changes": [],
        "avg_fitness_change": round(
            snap_b.get("avg_fitness", 0) - snap_a.get("avg_fitness", 0), 4
        ),
        "generation_gap": gen_b - gen_a,
    }

    # Gene-level diff using current genome data (born-generation based)
    genes = genome.get("genes", {})
    for name, data in genes.items():
        born = data.get("generation_born", 0)
        if gen_a < born <= gen_b:
            result["genes_added"].append(name)

    # Detect fitness movement for genes that existed before gen_a
    for name, data in genes.items():
        born = data.get("generation_born", 0)
        if born <= gen_a:
            record = data.get("contribution_record", [])
            if len(record) >= 2:
                old_fitness = 0.5
                for entry in record[:max(1, len(record) // 2)]:
                    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                        old_fitness += 0.05 if entry[1] else -0.02
                new_fitness = data.get("fitness", 0.5)
                delta = round(new_fitness - old_fitness, 4)
                if abs(delta) > 0.01:
                    result["fitness_changes"].append({
                        "name": name,
                        "old": round(old_fitness, 4),
                        "new": round(new_fitness, 4),
                        "delta": delta,
                    })

    # Sort fitness changes by absolute delta descending
    result["fitness_changes"].sort(key=lambda x: abs(x["delta"]), reverse=True)

    return result


# =========================================================================
# CLI Formatters
# =========================================================================


def _print_table(lineage: list[GeneLineage]) -> None:
    """Print lineage as a formatted ASCII table."""
    table = format_lineage_table(lineage)
    if not table:
        print("(empty genome)")
        return

    # Column widths
    headers = ["Name", "Parent", "Gen", "Fitness", "Trend", "Status", "Type", "Inv", "Children"]
    rows = []
    for row in table:
        rows.append([
            row["name"][:40],
            (row["parent"] or "")[:25],
            str(row["generation_born"]),
            f"{row['fitness']:.4f}",
            row["trend"],
            row["status"],
            row["component_type"],
            str(row["invocations"]),
            str(row["children_count"]),
        ])

    # Calculate column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    # Print
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    separator = "  ".join("-" * widths[i] for i in range(len(headers)))
    print(header_line)
    print(separator)
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def _print_diff(diff: dict) -> None:
    """Print generation diff in a readable format."""
    print(f"Generation diff: {diff['from_generation']} -> {diff['to_generation']} "
          f"(gap: {diff['generation_gap']})")
    print(f"Average fitness change: {diff['avg_fitness_change']:+.4f}")
    print()

    if diff["genes_added"]:
        print(f"Genes added ({len(diff['genes_added'])}):")
        for name in sorted(diff["genes_added"]):
            print(f"  + {name}")
        print()

    if diff["genes_removed"]:
        print(f"Genes removed ({len(diff['genes_removed'])}):")
        for name in sorted(diff["genes_removed"]):
            print(f"  - {name}")
        print()

    if diff["fitness_changes"]:
        print(f"Fitness changes ({len(diff['fitness_changes'])}):")
        for change in diff["fitness_changes"][:20]:
            direction = "+" if change["delta"] > 0 else ""
            print(f"  {change['name']}: {change['old']:.4f} -> {change['new']:.4f} "
                  f"({direction}{change['delta']:.4f})")
    else:
        print("No significant fitness changes detected.")


# =========================================================================
# CLI Entry Point
# =========================================================================


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    args = argv if argv is not None else sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0

    command = args[0]
    genome = load_genome()

    if not genome:
        print("No genome.json found. Run a converge cycle first.")
        return 1

    if command == "tree":
        lineage = build_lineage(genome)
        print(format_lineage_tree(lineage))
        return 0

    elif command == "table":
        lineage = build_lineage(genome)
        _print_table(lineage)
        return 0

    elif command == "diff":
        gen_a = int(args[1]) if len(args) > 1 else None
        gen_b = int(args[2]) if len(args) > 2 else None
        diff = gene_diff(gen_a, gen_b, genome)
        _print_diff(diff)
        return 0

    else:
        print(f"Unknown command: {command}")
        print("Usage: python3 lib/genome_lineage.py [tree|table|diff]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
