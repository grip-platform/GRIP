"""Gene Phylogeny Tree -- ASCII ancestry visualization of genome evolution.

Reads genome data from data/converge-dna/ and renders evolutionary trees,
lineage traces, extinction reports, fitness trajectories, and speciation events.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# -- Data structures ----------------------------------------------------------

@dataclass
class FitnessPoint:
    """A single fitness observation for a gene at a generation."""

    generation: int
    fitness: float
    invocations: int


@dataclass
class PhyloNode:
    """A node in the phylogenetic tree."""

    name: str
    generation: int
    fitness: float
    invocations: int = 0
    status: str = "active"  # active | dormant | extinct
    lineage: str = "built-in"
    parent: Optional["PhyloNode"] = field(default=None, repr=False)
    children: list["PhyloNode"] = field(default_factory=list)

    @property
    def status_icon(self) -> str:
        icons = {"active": "\u25cf", "dormant": "\u25cb", "extinct": "\u2717"}
        return icons.get(self.status, "?")

    @property
    def label(self) -> str:
        base = f"{self.name} [{self.fitness:.2f}] {self.status_icon}"
        if self.status in ("dormant", "extinct"):
            base += f" ({self.status})"
        return base


@dataclass
class PhyloTree:
    """A complete phylogenetic tree."""

    roots: list[PhyloNode] = field(default_factory=list)
    generation_count: int = 0
    total_genes: int = 0


# -- Genome I/O --------------------------------------------------------------

def load_genomes(data_dir: str | Path) -> list[dict]:
    """Load all genome JSON files from *data_dir*, sorted by generation."""
    data_path = Path(data_dir)
    if not data_path.exists():
        return []

    genomes: list[dict] = []
    for fp in sorted(data_path.glob("*.json")):
        try:
            with open(fp) as f:
                data = json.load(f)
            # Accept both single-genome and list-of-genomes files
            if isinstance(data, list):
                genomes.extend(data)
            else:
                genomes.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    genomes.sort(key=lambda g: g.get("generation", 0))
    return genomes


# -- Tree construction --------------------------------------------------------

def _parse_parent_from_lineage(lineage: str) -> Optional[str]:
    """Extract parent gene name from a lineage string.

    Examples:
        "micro-skill from converge"  -> "converge"
        "split from code-mode"       -> "code-mode"
        "derived from analysis-mode" -> "analysis-mode"
        "built-in"                   -> None
    """
    lineage_lower = lineage.lower().strip()
    for prefix in ("micro-skill from ", "split from ", "derived from ",
                    "evolved from ", "fork of ", "variant of "):
        if lineage_lower.startswith(prefix):
            return lineage[len(prefix):].strip()
    return None


def build_phylogeny(genomes: list[dict]) -> PhyloTree:
    """Construct a PhyloTree from a list of genome dicts.

    Each genome dict must have at minimum:
        {"generation": int, "genes": [{"name": str, "fitness": float, ...}]}

    Genes are linked into parent-child relationships via their ``lineage``
    field.  The latest observation of each gene wins (highest generation).
    """
    if not genomes:
        return PhyloTree()

    # Collect the latest snapshot of every gene seen across all generations.
    gene_latest: dict[str, dict] = {}
    max_gen = 0
    for genome in genomes:
        gen = genome.get("generation", 0)
        max_gen = max(max_gen, gen)
        for gene in genome.get("genes", []):
            name = gene.get("name", "")
            if not name:
                continue
            prev = gene_latest.get(name)
            if prev is None or gen >= prev.get("_gen", -1):
                gene_latest[name] = {**gene, "_gen": gen}

    # Create PhyloNode for each unique gene.
    nodes: dict[str, PhyloNode] = {}
    for name, gdata in gene_latest.items():
        nodes[name] = PhyloNode(
            name=name,
            generation=gdata.get("_gen", 0),
            fitness=gdata.get("fitness", 0.0),
            invocations=gdata.get("invocations", 0),
            status=gdata.get("status", "active"),
            lineage=gdata.get("lineage", "built-in"),
        )

    # Wire parent-child edges.
    for name, node in nodes.items():
        parent_name = _parse_parent_from_lineage(node.lineage)
        if parent_name and parent_name in nodes:
            parent_node = nodes[parent_name]
            node.parent = parent_node
            if node not in parent_node.children:
                parent_node.children.append(node)

    # Sort children alphabetically for deterministic output.
    for node in nodes.values():
        node.children.sort(key=lambda n: n.name)

    # Roots are nodes with no parent.
    roots = sorted(
        [n for n in nodes.values() if n.parent is None],
        key=lambda n: n.name,
    )

    gen_count = max_gen + 1 if genomes else 0
    return PhyloTree(roots=roots, generation_count=gen_count, total_genes=len(nodes))


# -- ASCII rendering ---------------------------------------------------------

def _render_node(node: PhyloNode, prefix: str, is_last: bool, lines: list[str]) -> None:
    """Recursively render a node and its children."""
    connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
    lines.append(f"{prefix}{connector}{node.label}")

    child_prefix = prefix + ("    " if is_last else "\u2502   ")
    for i, child in enumerate(node.children):
        _render_node(child, child_prefix, i == len(node.children) - 1, lines)


def render_tree(tree: PhyloTree) -> str:
    """Render a PhyloTree as an ASCII art string."""
    if not tree.roots:
        return "(empty tree)"

    lines: list[str] = []
    lines.append(f"Gene Phylogeny ({tree.total_genes} genes, {tree.generation_count} generations)")
    lines.append("")

    for i, root in enumerate(tree.roots):
        is_last = i == len(tree.roots) - 1
        _render_node(root, "", is_last, lines)

    return "\n".join(lines)


# -- Lineage tracing ---------------------------------------------------------

def trace_lineage(gene_name: str, genomes: list[dict]) -> list[FitnessPoint]:
    """Return the fitness trajectory of *gene_name* across all generations.

    One FitnessPoint per generation where the gene appears.
    """
    points: list[FitnessPoint] = []
    seen_gens: set[int] = set()

    for genome in sorted(genomes, key=lambda g: g.get("generation", 0)):
        gen = genome.get("generation", 0)
        for gene in genome.get("genes", []):
            if gene.get("name") == gene_name and gen not in seen_gens:
                seen_gens.add(gen)
                points.append(FitnessPoint(
                    generation=gen,
                    fitness=gene.get("fitness", 0.0),
                    invocations=gene.get("invocations", 0),
                ))
    return points


def render_lineage(gene_name: str, points: list[FitnessPoint]) -> str:
    """Render a gene's lineage as a text timeline."""
    if not points:
        return f"Gene '{gene_name}' not found in any generation."

    lines = [f"Lineage: {gene_name}", ""]
    for pt in points:
        bar_len = int(pt.fitness * 20)
        bar = "\u2588" * bar_len + "\u2591" * (20 - bar_len)
        lines.append(
            f"  Gen {pt.generation:>3}: {bar} {pt.fitness:.2f}  "
            f"({pt.invocations} invocations)"
        )
    return "\n".join(lines)


# -- Extinction detection -----------------------------------------------------

def find_extinctions(genomes: list[dict]) -> list[dict]:
    """Find genes that went dormant or extinct.

    Returns a list of dicts: {"name", "last_generation", "last_fitness", "status"}.
    Sorted by name.
    """
    gene_latest: dict[str, dict] = {}
    for genome in genomes:
        gen = genome.get("generation", 0)
        for gene in genome.get("genes", []):
            name = gene.get("name", "")
            if not name:
                continue
            prev = gene_latest.get(name)
            if prev is None or gen >= prev["generation"]:
                gene_latest[name] = {
                    "name": name,
                    "generation": gen,
                    "fitness": gene.get("fitness", 0.0),
                    "status": gene.get("status", "active"),
                }

    extinct = [
        {
            "name": g["name"],
            "last_generation": g["generation"],
            "last_fitness": g["fitness"],
            "status": g["status"],
        }
        for g in gene_latest.values()
        if g["status"] in ("dormant", "extinct")
    ]
    extinct.sort(key=lambda e: e["name"])
    return extinct


def render_extinctions(extinctions: list[dict]) -> str:
    """Render extinction report."""
    if not extinctions:
        return "No dormant or extinct genes found."

    icon_map = {"dormant": "\u25cb", "extinct": "\u2717"}
    lines = [f"Extinctions & Dormancy ({len(extinctions)} genes)", ""]
    for ext in extinctions:
        icon = icon_map.get(ext["status"], "?")
        lines.append(
            f"  {icon} {ext['name']} -- {ext['status']} "
            f"(last seen gen {ext['last_generation']}, fitness {ext['last_fitness']:.2f})"
        )
    return "\n".join(lines)


# -- Fitness trajectory -------------------------------------------------------

def fitness_trajectory(gene_name: str, genomes: list[dict]) -> list[FitnessPoint]:
    """Alias for trace_lineage -- returns fitness over generations."""
    return trace_lineage(gene_name, genomes)


def render_fitness(gene_name: str, points: list[FitnessPoint]) -> str:
    """Render a sparkline-style fitness trajectory."""
    if not points:
        return f"Gene '{gene_name}' not found."

    lines = [f"Fitness Trajectory: {gene_name}", ""]

    # Sparkline
    spark_chars = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    sparks = []
    for pt in points:
        idx = min(int(pt.fitness * (len(spark_chars) - 1)), len(spark_chars) - 1)
        sparks.append(spark_chars[idx])
    lines.append(f"  {''.join(sparks)}")
    lines.append(f"  Gen {points[0].generation} -> Gen {points[-1].generation}")
    lines.append(f"  Min: {min(p.fitness for p in points):.2f}  "
                 f"Max: {max(p.fitness for p in points):.2f}  "
                 f"Latest: {points[-1].fitness:.2f}")
    return "\n".join(lines)


# -- Speciation events --------------------------------------------------------

def find_speciation_events(genomes: list[dict]) -> list[dict]:
    """Find speciation events -- when a gene spawned child genes.

    Returns list of dicts: {"parent", "child", "generation", "lineage"}.
    Sorted by generation then child name.
    """
    events: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for genome in sorted(genomes, key=lambda g: g.get("generation", 0)):
        gen = genome.get("generation", 0)
        for gene in genome.get("genes", []):
            name = gene.get("name", "")
            lineage = gene.get("lineage", "built-in")
            parent_name = _parse_parent_from_lineage(lineage)
            if parent_name and (parent_name, name) not in seen:
                seen.add((parent_name, name))
                events.append({
                    "parent": parent_name,
                    "child": name,
                    "generation": gen,
                    "lineage": lineage,
                })

    events.sort(key=lambda e: (e["generation"], e["child"]))
    return events


def render_speciation(events: list[dict]) -> str:
    """Render speciation events."""
    if not events:
        return "No speciation events found."

    lines = [f"Speciation Events ({len(events)})", ""]
    for ev in events:
        lines.append(
            f"  Gen {ev['generation']:>3}: {ev['parent']} -> {ev['child']}  "
            f"({ev['lineage']})"
        )
    return "\n".join(lines)


# -- CLI ----------------------------------------------------------------------

def build_cli() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="Gene Phylogeny Tree -- visualize genome evolution",
    )
    parser.add_argument(
        "--data-dir",
        default="data/converge-dna",
        help="Path to genome data directory (default: data/converge-dna)",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("tree", help="Show full phylogenetic tree")

    lin = sub.add_parser("lineage", help="Trace a gene's lineage")
    lin.add_argument("gene", help="Gene name to trace")

    sub.add_parser("extinctions", help="Show dormant/extinct genes")

    fit = sub.add_parser("fitness", help="Show fitness trajectory")
    fit.add_argument("gene", help="Gene name")

    sub.add_parser("speciation", help="Show speciation events")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_cli()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    genomes = load_genomes(args.data_dir)

    if args.command == "tree":
        tree = build_phylogeny(genomes)
        print(render_tree(tree))

    elif args.command == "lineage":
        points = trace_lineage(args.gene, genomes)
        print(render_lineage(args.gene, points))

    elif args.command == "extinctions":
        exts = find_extinctions(genomes)
        print(render_extinctions(exts))

    elif args.command == "fitness":
        points = fitness_trajectory(args.gene, genomes)
        print(render_fitness(args.gene, points))

    elif args.command == "speciation":
        events = find_speciation_events(genomes)
        print(render_speciation(events))

    return 0


if __name__ == "__main__":
    sys.exit(main())
