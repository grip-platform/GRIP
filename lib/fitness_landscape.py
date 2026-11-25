#!/usr/bin/env python3
"""Gene fitness landscape -- 2D ASCII terrain visualisation.

Visualises gene fitness as a 2D terrain where:
- X-axis = invocations (usage count)
- Y-axis = fitness score (0.0 - 1.0)

Supports peak, valley, plateau, and rising-star detection,
plus cross-generation landscape comparison.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


# Terrain characters ordered by density
TERRAIN_CHARS = (" ", ".", "\u00b7", "+", "#", "@")


@dataclass
class GenePoint:
    """A single gene positioned on the fitness landscape."""

    name: str
    fitness: float
    invocations: int
    status: str = "active"

    def __post_init__(self) -> None:
        if not (0.0 <= self.fitness <= 1.0):
            raise ValueError(
                f"fitness must be between 0.0 and 1.0, got {self.fitness}"
            )
        if self.invocations < 0:
            raise ValueError(
                f"invocations must be non-negative, got {self.invocations}"
            )
        if self.status not in ("active", "dormant"):
            raise ValueError(
                f"status must be 'active' or 'dormant', got {self.status!r}"
            )


@dataclass
class Landscape:
    """A 2D fitness landscape containing gene points."""

    genes: List[GenePoint] = field(default_factory=list)
    width: int = 60
    height: int = 20

    @property
    def max_invocations(self) -> int:
        if not self.genes:
            return 0
        return max(g.invocations for g in self.genes)


def build_landscape(
    genome_data: dict,
    width: int = 60,
    height: int = 20,
) -> Landscape:
    """Build a Landscape from genome JSON data.

    Expected format::

        {
            "id": "g1",
            "generation": 5,
            "genes": [
                {"name": "converge", "fitness": 0.85,
                 "invocations": 42, "status": "active"},
                ...
            ]
        }
    """
    genes: List[GenePoint] = []
    for g in genome_data.get("genes", []):
        genes.append(
            GenePoint(
                name=g["name"],
                fitness=float(g["fitness"]),
                invocations=int(g["invocations"]),
                status=g.get("status", "active"),
            )
        )
    return Landscape(genes=genes, width=width, height=height)


def _map_gene_to_grid(
    gene: GenePoint,
    max_inv: int,
    width: int,
    height: int,
) -> Tuple[int, int]:
    """Map a gene to grid coordinates (col, row).

    Col 0 is leftmost, row 0 is top (fitness=1.0).
    """
    if max_inv == 0:
        col = 0
    else:
        col = round(gene.invocations / max_inv * (width - 1))
    col = max(0, min(col, width - 1))

    row = round((1.0 - gene.fitness) * (height - 1))
    row = max(0, min(row, height - 1))
    return col, row


def _density_char(value: float) -> str:
    """Return a terrain character based on density value (0.0-1.0)."""
    idx = min(int(value * len(TERRAIN_CHARS)), len(TERRAIN_CHARS) - 1)
    return TERRAIN_CHARS[idx]


def render_terrain(
    landscape: Landscape,
    width: int = 60,
    height: int = 20,
) -> str:
    """Render a landscape as an ASCII terrain map.

    Returns a multi-line string with Y-axis (fitness) labels on the left
    and X-axis (invocations) labels on the bottom.
    """
    w = width
    h = height
    max_inv = landscape.max_invocations
    if max_inv == 0:
        max_inv = 1  # Avoid division by zero for rendering

    # Build density grid -- start empty
    grid: List[List[str]] = [[" "] * w for _ in range(h)]

    # Place gene influence: each gene radiates density to nearby cells
    density = [[0.0] * w for _ in range(h)]
    label_positions: List[Tuple[int, int, str]] = []

    for gene in landscape.genes:
        col, row = _map_gene_to_grid(gene, max_inv, w, h)
        label_positions.append((col, row, gene.name))

        # Radiate density from gene position
        radius = max(2, int(gene.fitness * 4))
        for dr in range(-radius, radius + 1):
            for dc in range(-radius, radius + 1):
                r2, c2 = row + dr, col + dc
                if 0 <= r2 < h and 0 <= c2 < w:
                    dist = math.sqrt(dr * dr + dc * dc)
                    if dist <= radius:
                        falloff = gene.fitness * (1.0 - dist / (radius + 1))
                        density[r2][c2] = max(density[r2][c2], falloff)

    # Convert density to terrain characters
    for r in range(h):
        for c in range(w):
            if density[r][c] > 0:
                grid[r][c] = _density_char(density[r][c])

    # Overlay gene labels -- shift left if label would overflow grid
    for col, row, name in label_positions:
        start = col
        if start + len(name) > w:
            start = max(0, w - len(name))
        for i, ch in enumerate(name):
            c2 = start + i
            if 0 <= c2 < w:
                grid[row][c2] = ch

    # Build output with Y-axis labels
    lines: List[str] = []
    y_labels = [1.0, 0.8, 0.6, 0.4, 0.2, 0.0]
    y_label_rows = {round((1.0 - y) * (h - 1)): f"{y:.1f}" for y in y_labels}

    for r in range(h):
        label = y_label_rows.get(r, "   ")
        label = f"{label:>4}"
        separator = "\u2502" if r < h - 1 else "\u2514"
        row_str = "".join(grid[r])
        lines.append(f"{label} {separator}{row_str}")

    # Bottom border
    lines.append("     " + "\u2500" * w)

    # X-axis labels
    x_ticks: List[int] = []
    if max_inv <= 1:
        x_ticks = [0]
    else:
        step = max(1, max_inv // 4)
        x_ticks = list(range(0, max_inv + 1, step))
        if x_ticks[-1] != max_inv:
            x_ticks.append(max_inv)

    x_label_parts: List[str] = []
    for tick in x_ticks:
        col_pos = round(tick / max_inv * (w - 1)) if max_inv > 0 else 0
        x_label_parts.append((col_pos, str(tick)))

    # Build x-axis label line
    x_line = [" "] * (w + 6)
    for col_pos, label_text in x_label_parts:
        pos = col_pos + 5  # offset for y-axis label width
        for i, ch in enumerate(label_text):
            if pos + i < len(x_line):
                x_line[pos + i] = ch

    lines.append("".join(x_line).rstrip())
    lines.append(" " * 5 + " " * (w // 2 - 7) + "invocations \u2192")

    return "\n".join(lines)


def find_peaks(landscape: Landscape) -> List[GenePoint]:
    """Find genes with fitness > 0.8 and invocations > 10."""
    return [
        g
        for g in landscape.genes
        if g.fitness > 0.8 and g.invocations > 10
    ]


def find_valleys(landscape: Landscape) -> List[GenePoint]:
    """Find genes with fitness < 0.3."""
    return [g for g in landscape.genes if g.fitness < 0.3]


def find_plateaus(landscape: Landscape) -> List[GenePoint]:
    """Find genes with >20 invocations but fitness between 0.4-0.6."""
    return [
        g
        for g in landscape.genes
        if g.invocations > 20 and 0.4 <= g.fitness <= 0.6
    ]


def find_rising_stars(landscape: Landscape) -> List[GenePoint]:
    """Find genes with <5 invocations but fitness > 0.7."""
    return [
        g
        for g in landscape.genes
        if g.invocations < 5 and g.fitness > 0.7
    ]


def compare_landscapes(
    gen1_data: dict,
    gen2_data: dict,
    width: int = 60,
    height: int = 20,
) -> str:
    """Compare two generations and show how the landscape shifted.

    Returns a formatted report with:
    - New genes (in gen2 but not gen1)
    - Removed genes (in gen1 but not gen2)
    - Fitness changes for shared genes
    - Both terrain renders side by side
    """
    ls1 = build_landscape(gen1_data, width, height)
    ls2 = build_landscape(gen2_data, width, height)

    names1 = {g.name: g for g in ls1.genes}
    names2 = {g.name: g for g in ls2.genes}

    new_genes = sorted(set(names2) - set(names1))
    removed_genes = sorted(set(names1) - set(names2))
    shared_genes = sorted(set(names1) & set(names2))

    lines: List[str] = []

    gen1_id = gen1_data.get("generation", "?")
    gen2_id = gen2_data.get("generation", "?")
    lines.append(f"Landscape Comparison: Gen {gen1_id} -> Gen {gen2_id}")
    lines.append("=" * 50)

    if new_genes:
        lines.append("")
        lines.append("New genes:")
        for name in new_genes:
            g = names2[name]
            lines.append(
                f"  + {name} (fitness={g.fitness:.2f}, inv={g.invocations})"
            )

    if removed_genes:
        lines.append("")
        lines.append("Removed genes:")
        for name in removed_genes:
            g = names1[name]
            lines.append(
                f"  - {name} (fitness={g.fitness:.2f}, inv={g.invocations})"
            )

    if shared_genes:
        lines.append("")
        lines.append("Changed genes:")
        for name in shared_genes:
            g1 = names1[name]
            g2 = names2[name]
            df = g2.fitness - g1.fitness
            di = g2.invocations - g1.invocations
            arrow = "\u2191" if df > 0 else ("\u2193" if df < 0 else "\u2192")
            sign_df = "+" if df >= 0 else ""
            sign_di = "+" if di >= 0 else ""
            lines.append(
                f"  {arrow} {name}: fitness {g1.fitness:.2f}->{g2.fitness:.2f} "
                f"({sign_df}{df:+.2f}), "
                f"inv {g1.invocations}->{g2.invocations} "
                f"({sign_di}{di:+d})"
            )

    lines.append("")
    lines.append(f"--- Gen {gen1_id} ---")
    lines.append(render_terrain(ls1, width, height))
    lines.append("")
    lines.append(f"--- Gen {gen2_id} ---")
    lines.append(render_terrain(ls2, width, height))

    return "\n".join(lines)


def _format_gene_table(
    genes: List[GenePoint], title: str
) -> str:
    """Format a list of genes as a simple table."""
    if not genes:
        return f"{title}: (none)\n"

    lines = [f"{title}:", ""]
    header = "  {:<25} {:>8} {:>12} {:>8}".format("Name", "Fitness", "Invocations", "Status")
    lines.append(header)
    lines.append("  " + "-" * 55)
    for g in sorted(genes, key=lambda x: -x.fitness):
        lines.append(
            f"  {g.name:<25} {g.fitness:>8.3f} {g.invocations:>12} {g.status:>8}"
        )
    lines.append("")
    return "\n".join(lines)


def build_cli() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="fitness_landscape",
        description="Gene fitness landscape -- 2D ASCII terrain visualisation",
    )
    sub = parser.add_subparsers(dest="command")

    # landscape
    ls_parser = sub.add_parser("landscape", help="Render full terrain map")
    ls_parser.add_argument("genome_file", help="Path to genome JSON file")
    ls_parser.add_argument(
        "--width", type=int, default=60, help="Terrain width (default: 60)"
    )
    ls_parser.add_argument(
        "--height", type=int, default=20, help="Terrain height (default: 20)"
    )

    # peaks
    pk_parser = sub.add_parser("peaks", help="Show peak genes")
    pk_parser.add_argument("genome_file", help="Path to genome JSON file")

    # valleys
    vl_parser = sub.add_parser("valleys", help="Show valley genes")
    vl_parser.add_argument("genome_file", help="Path to genome JSON file")

    # rising-stars
    rs_parser = sub.add_parser("rising-stars", help="Show rising star genes")
    rs_parser.add_argument("genome_file", help="Path to genome JSON file")

    # plateaus
    pl_parser = sub.add_parser("plateaus", help="Show plateau genes")
    pl_parser.add_argument("genome_file", help="Path to genome JSON file")

    # compare
    cmp_parser = sub.add_parser(
        "compare", help="Compare landscapes between generations"
    )
    cmp_parser.add_argument("gen1_file", help="Path to gen1 genome JSON")
    cmp_parser.add_argument("gen2_file", help="Path to gen2 genome JSON")
    cmp_parser.add_argument(
        "--width", type=int, default=60, help="Terrain width (default: 60)"
    )
    cmp_parser.add_argument(
        "--height", type=int, default=20, help="Terrain height (default: 20)"
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = build_cli()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "compare":
        gen1_data = json.loads(Path(args.gen1_file).read_text())
        gen2_data = json.loads(Path(args.gen2_file).read_text())
        width = getattr(args, "width", 60)
        height = getattr(args, "height", 20)
        print(compare_landscapes(gen1_data, gen2_data, width, height))
        return 0

    genome_data = json.loads(Path(args.genome_file).read_text())
    landscape = build_landscape(genome_data)

    if args.command == "landscape":
        width = getattr(args, "width", 60)
        height = getattr(args, "height", 20)
        print(render_terrain(landscape, width, height))
    elif args.command == "peaks":
        genes = find_peaks(landscape)
        print(_format_gene_table(genes, "Peak Genes (fitness>0.8, inv>10)"))
    elif args.command == "valleys":
        genes = find_valleys(landscape)
        print(_format_gene_table(genes, "Valley Genes (fitness<0.3)"))
    elif args.command == "rising-stars":
        genes = find_rising_stars(landscape)
        print(
            _format_gene_table(genes, "Rising Stars (inv<5, fitness>0.7)")
        )
    elif args.command == "plateaus":
        genes = find_plateaus(landscape)
        print(
            _format_gene_table(
                genes, "Plateau Genes (inv>20, 0.4<=fitness<=0.6)"
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
