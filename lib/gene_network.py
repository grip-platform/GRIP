#!/usr/bin/env python3
"""Gene co-expression network analysis for the GRIP genome.

Analyzes gene co-occurrence patterns across converge runs to discover
which genes fire together and how effective those combinations are.

Usage:
    python3 lib/gene_network.py analyze [--json]
    python3 lib/gene_network.py matrix [--json]
    python3 lib/gene_network.py hubs [--json]
    python3 lib/gene_network.py synergy gene_a gene_b [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

METRICS_PATH = Path(__file__).resolve().parent.parent / "learning" / "converge-metrics.jsonl"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Edge:
    gene_a: str
    gene_b: str
    weight: int = 0  # co-occurrence count
    success_rate: float = 0.0  # fraction of co-occurrences where run succeeded

    @property
    def key(self) -> tuple[str, str]:
        return (self.gene_a, self.gene_b)


@dataclass
class CoExpressionNetwork:
    edges: list[Edge] = field(default_factory=list)
    gene_counts: dict[str, int] = field(default_factory=dict)  # per-gene total occurrences

    def get_edge(self, gene_a: str, gene_b: str) -> Edge | None:
        a, b = sorted([gene_a, gene_b])
        for e in self.edges:
            if e.gene_a == a and e.gene_b == b:
                return e
        return None

    def neighbors(self, gene: str, min_weight: int = 1) -> list[str]:
        """Return genes connected to *gene* with weight >= min_weight."""
        result: list[str] = []
        for e in self.edges:
            if e.weight < min_weight:
                continue
            if e.gene_a == gene:
                result.append(e.gene_b)
            elif e.gene_b == gene:
                result.append(e.gene_a)
        return result


@dataclass
class Cluster:
    genes: list[str] = field(default_factory=list)
    avg_internal_weight: float = 0.0


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def load_runs(metrics_path: str | Path = METRICS_PATH) -> list[dict]:
    """Load converge metrics and return runs that contain gene data.

    A "run" is built by joining converge_start (expressed_genes) with
    converge_end (invoked_genes, satisfied) on run_id.
    """
    metrics_path = Path(metrics_path)
    if not metrics_path.exists():
        return []

    starts: dict[str, dict] = {}
    ends: dict[str, dict] = {}

    with open(metrics_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = rec.get("event", "")
            run_id = rec.get("run_id", "")
            if not run_id:
                continue
            if event == "converge_start" and "expressed_genes" in rec:
                starts[run_id] = rec
            elif event == "converge_end" and "invoked_genes" in rec:
                ends[run_id] = rec

    runs: list[dict] = []
    for run_id, start in starts.items():
        end = ends.get(run_id)
        invoked = end.get("invoked_genes", []) if end else []
        satisfied = end.get("satisfied", False) if end else False
        final_score = end.get("final_score", 0.0) if end else 0.0
        # Use invoked_genes as the gene set (actually fired during the run)
        if invoked:
            runs.append({
                "run_id": run_id,
                "expressed_genes": start.get("expressed_genes", []),
                "invoked_genes": invoked,
                "satisfied": satisfied,
                "final_score": final_score,
            })
    return runs


def build_network(runs: list[dict]) -> CoExpressionNetwork:
    """Build co-occurrence graph from invoked_genes across runs."""
    edge_map: dict[tuple[str, str], list[bool]] = {}  # (a, b) -> [satisfied, ...]
    gene_counts: dict[str, int] = {}

    for run in runs:
        genes = sorted(set(run.get("invoked_genes", [])))
        satisfied = run.get("satisfied", False)

        for g in genes:
            gene_counts[g] = gene_counts.get(g, 0) + 1

        for a, b in combinations(genes, 2):
            key = (a, b)  # already sorted
            if key not in edge_map:
                edge_map[key] = []
            edge_map[key].append(satisfied)

    edges: list[Edge] = []
    for (a, b), outcomes in edge_map.items():
        weight = len(outcomes)
        success_rate = sum(1 for o in outcomes if o) / weight if weight else 0.0
        edges.append(Edge(gene_a=a, gene_b=b, weight=weight, success_rate=success_rate))

    edges.sort(key=lambda e: (-e.weight, e.gene_a, e.gene_b))
    return CoExpressionNetwork(edges=edges, gene_counts=gene_counts)


def find_clusters(network: CoExpressionNetwork, min_weight: int = 2) -> list[Cluster]:
    """Greedy clustering: start with highest-weight edge, expand by adding
    connected genes with weight >= min_weight."""
    used: set[str] = set()
    clusters: list[Cluster] = []

    # Edges already sorted by descending weight
    for seed_edge in network.edges:
        if seed_edge.weight < min_weight:
            continue
        if seed_edge.gene_a in used and seed_edge.gene_b in used:
            continue

        cluster_genes: list[str] = []
        for g in [seed_edge.gene_a, seed_edge.gene_b]:
            if g not in used:
                cluster_genes.append(g)
                used.add(g)

        if not cluster_genes:
            continue

        # Expand: try adding neighbors of current cluster members
        changed = True
        while changed:
            changed = False
            for g in list(cluster_genes):
                for neighbor in network.neighbors(g, min_weight=min_weight):
                    if neighbor not in used:
                        cluster_genes.append(neighbor)
                        used.add(neighbor)
                        changed = True

        # Compute average internal weight
        internal_weights: list[int] = []
        for a, b in combinations(sorted(cluster_genes), 2):
            edge = network.get_edge(a, b)
            if edge:
                internal_weights.append(edge.weight)

        avg_w = sum(internal_weights) / len(internal_weights) if internal_weights else 0.0
        clusters.append(Cluster(genes=sorted(cluster_genes), avg_internal_weight=avg_w))

    clusters.sort(key=lambda c: -c.avg_internal_weight)
    return clusters


def hub_genes(network: CoExpressionNetwork, top_n: int = 5) -> list[tuple[str, int]]:
    """Genes with most connections (highest degree)."""
    degree: dict[str, int] = {}
    for e in network.edges:
        if e.weight > 0:
            degree[e.gene_a] = degree.get(e.gene_a, 0) + 1
            degree[e.gene_b] = degree.get(e.gene_b, 0) + 1
    ranked = sorted(degree.items(), key=lambda x: (-x[1], x[0]))
    return ranked[:top_n]


def isolated_genes(network: CoExpressionNetwork, all_genes: list[str]) -> list[str]:
    """Genes that appear in the genome but never co-occur with others."""
    connected: set[str] = set()
    for e in network.edges:
        if e.weight > 0:
            connected.add(e.gene_a)
            connected.add(e.gene_b)
    return sorted(g for g in all_genes if g not in connected)


def synergy_score(network: CoExpressionNetwork, gene_a: str, gene_b: str) -> float:
    """Co-occurrence weight * success_rate when together."""
    edge = network.get_edge(gene_a, gene_b)
    if not edge:
        return 0.0
    return edge.weight * edge.success_rate


def format_adjacency(network: CoExpressionNetwork) -> str:
    """ASCII matrix showing co-occurrence counts."""
    genes = sorted(network.gene_counts.keys())
    if not genes:
        return "(empty network)"

    # Build lookup
    lookup: dict[tuple[str, str], int] = {}
    for e in network.edges:
        lookup[(e.gene_a, e.gene_b)] = e.weight
        lookup[(e.gene_b, e.gene_a)] = e.weight

    # Truncate long gene names for display
    max_label = 20
    labels = [g[:max_label] for g in genes]
    col_width = max(len(l) for l in labels) + 1

    # Header row
    header = " " * col_width + "".join(l.rjust(col_width) for l in labels)
    lines = [header]

    for i, g_row in enumerate(genes):
        row_cells: list[str] = []
        for j, g_col in enumerate(genes):
            if i == j:
                row_cells.append("-".rjust(col_width))
            else:
                val = lookup.get((g_row, g_col), 0)
                row_cells.append(str(val).rjust(col_width) if val else ".".rjust(col_width))
        lines.append(labels[i].rjust(col_width) + "".join(row_cells))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_common_args(p: argparse.ArgumentParser) -> None:
    """Add --json and --metrics flags shared by all subcommands."""
    p.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    p.add_argument("--metrics", default=str(METRICS_PATH), help="Path to converge-metrics.jsonl")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gene co-expression network analysis for the GRIP genome"
    )
    sub = parser.add_subparsers(dest="command")

    analyze = sub.add_parser("analyze", help="Network summary, clusters, hub genes")
    _add_common_args(analyze)

    matrix = sub.add_parser("matrix", help="Adjacency matrix")
    _add_common_args(matrix)

    hubs = sub.add_parser("hubs", help="Top hub genes")
    _add_common_args(hubs)

    syn = sub.add_parser("synergy", help="Synergy score between two genes")
    syn.add_argument("gene_a", help="First gene")
    syn.add_argument("gene_b", help="Second gene")
    _add_common_args(syn)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    runs = load_runs(args.metrics)
    network = build_network(runs)

    if args.command == "analyze":
        clusters = find_clusters(network)
        hubs = hub_genes(network)
        isolated = isolated_genes(network, sorted(network.gene_counts.keys()))

        if args.json:
            print(json.dumps({
                "total_genes": len(network.gene_counts),
                "total_edges": len(network.edges),
                "total_runs": len(runs),
                "clusters": [{"genes": c.genes, "avg_internal_weight": round(c.avg_internal_weight, 2)} for c in clusters],
                "hub_genes": [{"gene": g, "degree": d} for g, d in hubs],
                "isolated_genes": isolated,
            }, indent=2))
        else:
            print(f"Gene Co-Expression Network")
            print(f"  Runs analyzed: {len(runs)}")
            print(f"  Genes: {len(network.gene_counts)}")
            print(f"  Edges: {len(network.edges)}")
            print()
            if clusters:
                print("Clusters:")
                for i, c in enumerate(clusters, 1):
                    print(f"  {i}. [{', '.join(c.genes)}] (avg weight: {c.avg_internal_weight:.1f})")
            if hubs:
                print("\nHub Genes:")
                for g, d in hubs:
                    print(f"  {g}: degree {d}")
            if isolated:
                print(f"\nIsolated Genes: {', '.join(isolated)}")
        return 0

    elif args.command == "matrix":
        if args.json:
            matrix: dict[str, dict[str, int]] = {}
            genes = sorted(network.gene_counts.keys())
            lookup: dict[tuple[str, str], int] = {}
            for e in network.edges:
                lookup[(e.gene_a, e.gene_b)] = e.weight
                lookup[(e.gene_b, e.gene_a)] = e.weight
            for g in genes:
                matrix[g] = {}
                for g2 in genes:
                    if g == g2:
                        matrix[g][g2] = 0
                    else:
                        matrix[g][g2] = lookup.get((g, g2), 0)
            print(json.dumps(matrix, indent=2))
        else:
            print(format_adjacency(network))
        return 0

    elif args.command == "hubs":
        hubs = hub_genes(network, top_n=10)
        if args.json:
            print(json.dumps([{"gene": g, "degree": d} for g, d in hubs], indent=2))
        else:
            print("Hub Genes (top connections):")
            for g, d in hubs:
                print(f"  {g}: degree {d}")
        return 0

    elif args.command == "synergy":
        score = synergy_score(network, args.gene_a, args.gene_b)
        if args.json:
            edge = network.get_edge(args.gene_a, args.gene_b)
            print(json.dumps({
                "gene_a": args.gene_a,
                "gene_b": args.gene_b,
                "synergy_score": round(score, 2),
                "weight": edge.weight if edge else 0,
                "success_rate": round(edge.success_rate, 2) if edge else 0.0,
            }, indent=2))
        else:
            print(f"Synergy({args.gene_a}, {args.gene_b}) = {score:.2f}")
            edge = network.get_edge(args.gene_a, args.gene_b)
            if edge:
                print(f"  Co-occurrences: {edge.weight}")
                print(f"  Success rate: {edge.success_rate:.0%}")
            else:
                print("  (no co-occurrence data)")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
