#!/usr/bin/env python3
"""Genome evolution animator -- generation-over-generation ASCII animation.

Shows genome evolution across generations as frame-by-frame ASCII animation.
Renders fitness bars, highlights new/extinct/changed genes, supports single
gene focus mode and generation range filtering.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GeneState:
    """State of a single gene in a generation."""
    name: str
    fitness: float
    invocations: int
    status: str  # active, dormant, new, extinct
    delta_fitness: float = 0.0


@dataclass
class GenomeFrame:
    """A single generation snapshot rendered as a frame."""
    generation: int
    genes: list[GeneState]
    timestamp: str = ""


@dataclass
class Animation:
    """A sequence of genome frames with a title."""
    frames: list[GenomeFrame]
    title: str = "Genome Evolution"


def _parse_gene(gene_dict: dict) -> GeneState:
    """Parse a gene dictionary into a GeneState."""
    return GeneState(
        name=gene_dict["name"],
        fitness=gene_dict.get("fitness", 0.0),
        invocations=gene_dict.get("invocations", 0),
        status=gene_dict.get("status", "active"),
        delta_fitness=gene_dict.get("delta_fitness", 0.0),
    )


def build_frames(genome_history: list[dict]) -> list[GenomeFrame]:
    """Convert genome snapshots to animation frames.

    Each snapshot is a dict with 'generation' and 'genes' keys.
    Genes are compared across consecutive generations to compute
    delta_fitness and detect new/extinct genes.
    """
    if not genome_history:
        return []

    sorted_history = sorted(genome_history, key=lambda s: s.get("generation", 0))
    frames: list[GenomeFrame] = []

    for i, snapshot in enumerate(sorted_history):
        genes_raw = snapshot.get("genes", [])
        if isinstance(genes_raw, dict):
            genes_raw = list(genes_raw.values())
        genes: list[GeneState] = []

        # Build lookup from previous generation
        prev_genes: dict[str, GeneState] = {}
        if i > 0:
            prev_snapshot = sorted_history[i - 1]
            prev_raw = prev_snapshot.get("genes", [])
            if isinstance(prev_raw, dict):
                prev_raw = list(prev_raw.values())
            for g in prev_raw:
                prev_genes[g["name"]] = _parse_gene(g)

        prev_names = set(prev_genes.keys())
        curr_names = {g["name"] for g in genes_raw}

        for g_dict in genes_raw:
            gene = _parse_gene(g_dict)
            if gene.name in prev_genes:
                gene.delta_fitness = gene.fitness - prev_genes[gene.name].fitness
                if gene.status != "extinct" and gene.status != "new":
                    gene.status = prev_genes[gene.name].status if gene.status == "active" else gene.status
                    # Keep original status but compute delta
                    gene.status = g_dict.get("status", "active")
            elif i > 0:
                # Gene not in previous generation -- it's new
                if gene.status not in ("extinct",):
                    gene.status = "new"
            genes.append(gene)

        # Detect extinct genes (in prev but not in curr)
        for name in prev_names - curr_names:
            pg = prev_genes[name]
            genes.append(GeneState(
                name=pg.name,
                fitness=0.0,
                invocations=0,
                status="extinct",
                delta_fitness=-pg.fitness,
            ))

        frame = GenomeFrame(
            generation=snapshot.get("generation", 0),
            genes=genes,
            timestamp=snapshot.get("timestamp", ""),
        )
        frames.append(frame)

    return frames


def _fitness_bar(fitness: float, bar_width: int = 20) -> str:
    """Render a fitness value as a bar of filled/empty blocks."""
    clamped = max(0.0, min(1.0, fitness))
    filled = round(clamped * bar_width)
    empty = bar_width - filled
    return "\u2588" * filled + "\u2591" * empty


def _gene_prefix(gene: GeneState) -> str:
    """Return the prefix character for a gene based on status/delta."""
    if gene.status == "new":
        return "+"
    if gene.status == "extinct":
        return "\u00d7"
    if gene.delta_fitness > 0.1:
        return "\u2191"
    if gene.delta_fitness < -0.1:
        return "\u2193"
    return " "


def _gene_suffix(gene: GeneState) -> str:
    """Return annotation suffix for changed genes."""
    if gene.status == "new":
        return " \u2190 NEW"
    if gene.status == "extinct":
        return " \u2190 EXTINCT"
    if gene.delta_fitness > 0.1:
        return f" \u2190 +{gene.delta_fitness:.2f}"
    if gene.delta_fitness < -0.1:
        return f" \u2190 {gene.delta_fitness:.2f}"
    return ""


def render_frame(frame: GenomeFrame, width: int = 60) -> str:
    """Render a single generation frame with fitness bars.

    Returns a multi-line string with box-drawing borders, gene names,
    fitness bars, and annotations for new/extinct/changed genes.
    """
    inner = width - 4  # account for border chars
    header_text = f" Generation {frame.generation} "
    # Top border
    top_fill = inner - len(header_text) - 2
    if top_fill < 0:
        top_fill = 0
    top = "\u2554\u2550\u2550" + header_text + "\u2550" * top_fill + "\u2557"

    lines = [top]

    name_width = 14  # fixed name column
    bar_width = 20
    # value_width = 4  # e.g. "0.85"

    for gene in frame.genes:
        prefix = _gene_prefix(gene)
        suffix = _gene_suffix(gene)
        name_display = gene.name[:name_width].ljust(name_width)
        bar = _fitness_bar(gene.fitness, bar_width)
        value = f"{gene.fitness:.2f}"
        content = f"{prefix} {name_display}{bar} {value}{suffix}"
        # Pad or truncate to inner width
        if len(content) > inner:
            content = content[:inner]
        else:
            content = content.ljust(inner)
        lines.append(f"\u2551 {content} \u2551")

    # Bottom border
    bottom = "\u255a" + "\u2550" * (width - 2) + "\u255d"
    lines.append(bottom)

    return "\n".join(lines)


def render_animation(animation: Animation) -> str:
    """Render all frames in sequence with title."""
    parts = [f"=== {animation.title} ===", ""]
    for frame in animation.frames:
        parts.append(render_frame(frame))
        parts.append("")
    return "\n".join(parts)


def focus_gene(animation: Animation, gene_name: str) -> Animation:
    """Filter animation to show only a single gene's journey across generations."""
    focused_frames: list[GenomeFrame] = []
    for frame in animation.frames:
        matching = [g for g in frame.genes if g.name == gene_name]
        if matching:
            focused_frames.append(GenomeFrame(
                generation=frame.generation,
                genes=matching,
                timestamp=frame.timestamp,
            ))
    return Animation(
        frames=focused_frames,
        title=f"{animation.title} -- focus: {gene_name}",
    )


def render_snapshot(frame: GenomeFrame) -> str:
    """Detailed single-generation view with full stats."""
    lines = [
        f"Generation {frame.generation}",
        f"Timestamp: {frame.timestamp}" if frame.timestamp else "",
        f"Genes: {len(frame.genes)}",
        "-" * 60,
    ]
    lines = [l for l in lines if l]

    active = [g for g in frame.genes if g.status == "active"]
    dormant = [g for g in frame.genes if g.status == "dormant"]
    new = [g for g in frame.genes if g.status == "new"]
    extinct = [g for g in frame.genes if g.status == "extinct"]

    for label, group in [("Active", active), ("Dormant", dormant), ("New", new), ("Extinct", extinct)]:
        if group:
            lines.append(f"\n[{label}]")
            for g in sorted(group, key=lambda x: -x.fitness):
                bar = _fitness_bar(g.fitness)
                delta_str = ""
                if g.delta_fitness != 0.0:
                    sign = "+" if g.delta_fitness > 0 else ""
                    delta_str = f" ({sign}{g.delta_fitness:.2f})"
                lines.append(f"  {g.name:<20} {bar} {g.fitness:.2f}  inv={g.invocations}{delta_str}")

    return "\n".join(lines)


def filter_generations(frames: list[GenomeFrame], from_gen: Optional[int] = None, to_gen: Optional[int] = None) -> list[GenomeFrame]:
    """Filter frames to a generation range (inclusive)."""
    result = frames
    if from_gen is not None:
        result = [f for f in result if f.generation >= from_gen]
    if to_gen is not None:
        result = [f for f in result if f.generation <= to_gen]
    return result


def load_genome_history(path: str) -> list[dict]:
    """Load genome history from a JSON file."""
    with open(path) as f:
        return json.load(f)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Genome evolution animator -- ASCII animation of genome changes",
    )
    subparsers = parser.add_subparsers(dest="command")

    # animate subcommand
    anim_parser = subparsers.add_parser("animate", help="Show genome evolution animation")
    anim_parser.add_argument("--file", "-f", required=True, help="Path to genome history JSON")
    anim_parser.add_argument("--from", dest="from_gen", type=int, default=None, help="Start generation (inclusive)")
    anim_parser.add_argument("--to", dest="to_gen", type=int, default=None, help="End generation (inclusive)")
    anim_parser.add_argument("--gene", type=str, default=None, help="Focus on a single gene")
    anim_parser.add_argument("--title", type=str, default="Genome Evolution", help="Animation title")

    # snapshot subcommand
    snap_parser = subparsers.add_parser("snapshot", help="Show detailed single-generation view")
    snap_parser.add_argument("generation", type=int, help="Generation number to display")
    snap_parser.add_argument("--file", "-f", required=True, help="Path to genome history JSON")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "animate":
        history = load_genome_history(args.file)
        frames = build_frames(history)
        frames = filter_generations(frames, args.from_gen, args.to_gen)
        animation = Animation(frames=frames, title=args.title)
        if args.gene:
            animation = focus_gene(animation, args.gene)
        print(render_animation(animation))
        return 0

    if args.command == "snapshot":
        history = load_genome_history(args.file)
        frames = build_frames(history)
        matching = [f for f in frames if f.generation == args.generation]
        if not matching:
            print(f"Generation {args.generation} not found", file=sys.stderr)
            return 1
        print(render_snapshot(matching[0]))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
