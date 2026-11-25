#!/usr/bin/env python3
"""genome_export.py -- Export portable genome subset for team sharing.

Filters to living genes (fitness > DEAD_THRESHOLD), strips sensitive
session-specific fields (commit SHAs, KONO state, ablation scores),
and writes a portable JSON file safe to commit unencrypted.

Usage:
    python3 lib/genome_export.py                      # export to data/genome-shared.json
    python3 lib/genome_export.py --output PATH        # custom output path
    python3 lib/genome_export.py --min-fitness 0.3    # raise filter threshold
    python3 lib/genome_export.py --stats              # print stats only, no file write
    python3 lib/genome_export.py --include-content    # include compressed_content in export
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
GENOME_PATH = CLAUDE_DIR / "cache" / "genome.json"
DEFAULT_OUTPUT = CLAUDE_DIR / "data" / "genome-shared.json"

DEAD_THRESHOLD = 0.2

# Fields stripped from each gene before export (session-specific / sensitive)
_STRIP_FIELDS = {
    "contribution_record",   # contains commit SHAs — leaks repo history
    "expression_contexts",   # session-specific invocation contexts
    "kono_attestations",     # KONO internal state
    "kono_permanent",        # KONO flag
    "ablation",              # session-specific A/B scoring
    "last_invoked",          # environment timestamp
    "invocation_count",      # environment-specific usage counter
}

# Fields preserved in the portable export
_KEEP_FIELDS = {
    "name",
    "content_hash",
    "component_type",
    "fitness",
    "lineage",
    "generation_born",
    "mutations",
    "domain_fitness",
    "compressed_content",
    "protected",
    "parameters",
}


def _load_genome(genome_path: Path) -> dict:
    """Load raw genome JSON. Raises FileNotFoundError if missing."""
    with open(genome_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _portable_gene(gene: dict) -> dict:
    """Return a copy of gene with sensitive fields stripped."""
    return {k: v for k, v in gene.items() if k in _KEEP_FIELDS}


def export_genome(
    genome_path: Path = GENOME_PATH,
    output_path: Path = DEFAULT_OUTPUT,
    min_fitness: float = DEAD_THRESHOLD,
    strip_compressed_content: bool = True,
) -> dict:
    """Export living genes to a portable, shareable JSON file.

    Args:
        genome_path: Source genome JSON path.
        output_path: Destination for the exported file.
        min_fitness: Only export genes with fitness strictly above this value.
        strip_compressed_content: Remove compressed_content from exported genes
            (default True). Reduces export size by ~87%. Pass False to include
            full content for hydration workflows.

    Returns:
        Summary dict: {exported, total, skipped, output_path}
    """
    genome = _load_genome(genome_path)
    genes_raw: dict = genome.get("genes", {})
    current_gen: int = genome.get("generation", 0)

    living = {}
    skipped = 0
    for name, gene in genes_raw.items():
        if gene.get("fitness", 0.0) > min_fitness:
            portable = _portable_gene(gene)
            if strip_compressed_content:
                portable.pop("compressed_content", None)
            living[name] = portable
        else:
            skipped += 1

    payload = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_from_gen": current_gen,
        "gene_count": len(living),
        "genes": living,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, output_path)

    return {
        "exported": len(living),
        "total": len(genes_raw),
        "skipped": skipped,
        "output_path": str(output_path),
    }


def export_stats(
    genome_path: Path = GENOME_PATH,
    min_fitness: float = DEAD_THRESHOLD,
) -> dict:
    """Return export statistics without writing any file."""
    genome = _load_genome(genome_path)
    genes_raw: dict = genome.get("genes", {})

    living = sum(1 for g in genes_raw.values() if g.get("fitness", 0.0) > min_fitness)
    dead = len(genes_raw) - living

    return {
        "total": len(genes_raw),
        "exportable": living,
        "dead_skipped": dead,
        "min_fitness": min_fitness,
        "generation": genome.get("generation", 0),
    }


def export_compact(
    genome_path: Path = GENOME_PATH,
    output_path: Path = None,
    min_fitness: float = DEAD_THRESHOLD,
) -> dict:
    """Export genome in compact positional array format.

    Each gene becomes [name, hash, type, fitness, gen_born, protected, domain_fitness].
    Header defines field positions (self-documenting). Eliminates ~55KB structural
    overhead from repeated JSON keys.

    Returns:
        Summary dict: {exported, total, size_bytes, output_path}
    """
    if output_path is None:
        output_path = CLAUDE_DIR / "data" / "genome-compact.json"

    genome = _load_genome(genome_path)
    genes_raw: dict = genome.get("genes", {})
    current_gen: int = genome.get("generation", 0)

    header = ["name", "content_hash", "component_type", "fitness",
              "generation_born", "protected", "domain_fitness"]

    rows = []
    skipped = 0
    for name, gene in genes_raw.items():
        fitness = gene.get("fitness", 0.0)
        if fitness > min_fitness:
            rows.append([
                name,
                gene.get("content_hash", ""),
                gene.get("component_type", "skill"),
                round(fitness, 4),
                gene.get("generation_born", 0),
                gene.get("protected", False),
                gene.get("domain_fitness", {}),
            ])
        else:
            skipped += 1

    payload = {
        "version": 2,
        "format": "compact-positional",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_from_gen": current_gen,
        "header": header,
        "gene_count": len(rows),
        "genes": rows,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, separators=(",", ":"))
    tmp = output_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, output_path)

    return {
        "exported": len(rows),
        "total": len(genes_raw),
        "skipped": skipped,
        "size_bytes": len(content),
        "output_path": str(output_path),
    }


def import_compact(compact_path: Path) -> dict:
    """Import a compact-format genome back to standard gene dicts.

    Returns a dict of {gene_name: gene_dict} suitable for Genome reconstruction.
    """
    with open(compact_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("format") != "compact-positional":
        raise ValueError("Not a compact-positional genome file")

    header = data["header"]
    genes = {}
    for row in data["genes"]:
        gene = dict(zip(header, row))
        genes[gene["name"]] = gene

    return genes


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export portable GRIP genome for team sharing")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output file path")
    parser.add_argument("--min-fitness", type=float, default=DEAD_THRESHOLD, help="Minimum fitness to export (default: 0.2)")
    parser.add_argument("--stats", action="store_true", help="Print stats only, no file write")
    parser.add_argument("--compact", action="store_true",
                        help="Export in compact positional array format (v2)")
    parser.add_argument("--include-content", action="store_true",
                        help="Include compressed_content in export (omitted by default)")
    args = parser.parse_args()

    if args.stats:
        stats = export_stats(min_fitness=args.min_fitness)
        print(json.dumps(stats, indent=2))
    elif args.compact:
        result = export_compact(min_fitness=args.min_fitness)
        print(
            f"Compact: {result['exported']} genes "
            f"({result['size_bytes']} bytes) -> {result['output_path']}"
        )
    else:
        result = export_genome(
            output_path=args.output,
            min_fitness=args.min_fitness,
            strip_compressed_content=not args.include_content,
        )
        print(
            f"Exported {result['exported']} living genes "
            f"(of {result['total']} total) -> {result['output_path']}"
        )
