#!/usr/bin/env python3
"""genome_breeder.py -- Cross-team genome breeding via portable crossover.

Breeds operator genomes from data/genomes/*.json using dict-level crossover
(no Genome object reconstruction needed). Offspring are imported via
genome_import.merge_genomes() with max-fitness merge and nursery caps.

The recursive loop: export -> breed -> import -> evolve (RSI) -> export -> ...

Usage:
    python3 lib/genome_breeder.py export                # Export to data/genomes/{operator}.json
    python3 lib/genome_breeder.py breed                 # Round-robin breed all
    python3 lib/genome_breeder.py breed --pair V>> K>>  # Directed pairing
    python3 lib/genome_breeder.py breed --strategy tournament --top 3
    python3 lib/genome_breeder.py breed --dry-run       # Preview without writing
    python3 lib/genome_breeder.py import                # Import _bred.json into local genome
    python3 lib/genome_breeder.py status                # Pool stats
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_LIB_DIR = str(Path(__file__).resolve().parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from json_utils import safe_load_json, safe_save_json

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
GENOME_PATH = CLAUDE_DIR / "cache" / "genome.json"
GENOMES_DIR = CLAUDE_DIR / "data" / "genomes"
BRED_OUTPUT = GENOMES_DIR / "_bred.json"
BREED_META = GENOMES_DIR / "_breed-meta.json"

# Breeding constants
BREED_FITNESS_THRESHOLD = 0.4  # Lower than runtime 0.6 to encourage gene exchange
KONO_PERMANENT_FLOOR = 0.6
NEUTRAL_FITNESS = 0.5
DEAD_THRESHOLD = 0.2
EXPORT_VERSION = 2


# =========================================================================
# Operator Detection
# =========================================================================

def _parse_identity(identity_path: Path) -> tuple:
    """Parse (signature, name) out of an identity.md. Empty strings when absent."""
    if not identity_path.exists():
        return "", ""
    try:
        content = identity_path.read_text(encoding="utf-8")
    except OSError:
        return "", ""
    sig = re.search(r'signature:\s*"([A-Z]>>)"', content)
    name = re.search(r'name:\s*"([^"]+)"', content)
    return (sig.group(1) if sig else ""), (name.group(1) if name else "")


def _git_user_name(cwd: Path) -> str:
    """git config user.name, scoped to cwd. Empty string when unavailable."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=5,
            cwd=str(cwd),
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def detect_operator(claude_dir: Path = CLAUDE_DIR) -> dict:
    """Detect the current operator from identity.md or git config.

    Every field resolves from the GIVEN claude_dir. get_username() reads the
    ambient CLAUDE_DIR and accepts no argument, so it serves only as the
    fallback for `name` — used when identity.md supplies none. That keeps
    get_username's own precedence (GRIP_USER, git config, os.getlogin) intact
    for the ambient case, while an explicit claude_dir now governs its own
    result instead of being silently ignored (#4280).

    Returns:
        dict with keys: signature (e.g. "V>>"), name (e.g. "laurie"),
        full_name (e.g. "V>>")
    """
    from lib.user_detection import get_username

    signature, identity_name = _parse_identity(claude_dir / "facts" / "identity.md")
    full_name = identity_name or _git_user_name(claude_dir) or "unknown"

    first = identity_name.split()
    operator_name = first[0].lower() if first else get_username()

    # Derive signature if not found in identity.md
    if not signature:
        signature = f"{operator_name[0].upper()}>>"

    return {
        "signature": signature,
        "name": operator_name,
        "full_name": full_name,
    }


# =========================================================================
# Export (Version 2 with operator metadata)
# =========================================================================

def export_operator_genome(
    genome_path: Path = GENOME_PATH,
    genomes_dir: Path = GENOMES_DIR,
    claude_dir: Path = CLAUDE_DIR,
) -> dict:
    """Export local genome to data/genomes/{operator}.json with operator metadata.

    Wraps genome_export.export_genome() and adds version 2 operator fields.

    Returns:
        Summary dict with operator info, gene count, and output path.
    """
    from lib.genome_export import export_genome as _base_export

    operator = detect_operator(claude_dir)
    output_path = genomes_dir / f"{operator['name']}.json"

    # Export using base exporter (strips sensitive fields + compressed_content)
    genomes_dir.mkdir(parents=True, exist_ok=True)
    result = _base_export(
        genome_path=genome_path,
        output_path=output_path,
        strip_compressed_content=True,
    )

    # Upgrade to version 2 format with operator metadata
    payload = safe_load_json(output_path)
    if payload is None:
        payload = {}

    # Compute genome fingerprint
    genes_json = json.dumps(payload.get("genes", {}), sort_keys=True)
    fingerprint = hashlib.sha256(genes_json.encode()).hexdigest()[:12]

    payload["version"] = EXPORT_VERSION
    payload["operator"] = operator["signature"]
    payload["operator_name"] = operator["name"]
    payload["genome_fingerprint"] = fingerprint

    tmp = output_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, output_path)

    return {
        "operator": operator["signature"],
        "operator_name": operator["name"],
        "exported": result["exported"],
        "total": result["total"],
        "fingerprint": fingerprint,
        "output_path": str(output_path),
    }


# =========================================================================
# Pool Loading
# =========================================================================

def load_genome_pool(genomes_dir: Path = GENOMES_DIR) -> list[dict]:
    """Load all operator genomes from data/genomes/.

    Skips files prefixed with _ (like _bred.json, _breed-meta.json).

    Returns:
        List of genome dicts, each with operator metadata.
    """
    pool = []
    if not genomes_dir.exists():
        return pool

    for path in sorted(genomes_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        genome = safe_load_json(path)
        if genome is None:
            continue
        # Ensure operator_name is set
        if "operator_name" not in genome:
            genome["operator_name"] = path.stem
        pool.append(genome)

    return pool


# =========================================================================
# Portable Crossover (dict-level)
# =========================================================================

def portable_crossover(
    genome_a: dict,
    genome_b: dict,
    fitness_threshold: float = BREED_FITNESS_THRESHOLD,
) -> dict:
    """Cross two portable genome dicts, producing an offspring dict.

    Strategy:
    1. Base = deepcopy of genome_a genes
    2. Genes only in B: add if fitness >= threshold
    3. Shared genes: keep higher fitness variant
    4. Track lineage as "breed:{operator_a}x{operator_b}"
    5. KONO-permanent genes keep fitness floor of 0.6

    Args:
        genome_a: First parent genome dict (version 1 or 2 format).
        genome_b: Second parent genome dict.
        fitness_threshold: Minimum fitness for B's unique genes to enter child.

    Returns:
        Offspring genome dict in version 2 format.
    """
    genes_a = genome_a.get("genes", {})
    genes_b = genome_b.get("genes", {})
    op_a = genome_a.get("operator_name", genome_a.get("operator", "unknown-a"))
    op_b = genome_b.get("operator_name", genome_b.get("operator", "unknown-b"))

    child_genes = deepcopy(genes_a)

    for name, gene_b in genes_b.items():
        # Skip delight-only genes (trading cards, easter eggs)
        if gene_b.get("delight", False) or gene_b.get("component_type") == "easter-egg":
            continue

        b_fitness = gene_b.get("fitness", 0.0)

        if name not in child_genes:
            # Unique to B: add if fit enough
            if b_fitness >= fitness_threshold:
                new_gene = deepcopy(gene_b)
                new_gene["lineage"] = f"breed:{op_a}x{op_b}"
                child_genes[name] = new_gene
        else:
            # Shared: keep higher fitness variant
            a_fitness = child_genes[name].get("fitness", 0.0)
            if b_fitness > a_fitness:
                new_gene = deepcopy(gene_b)
                new_gene["lineage"] = f"breed:{op_a}x{op_b}"
                child_genes[name] = new_gene

    # Apply KONO floor protection
    for gene in child_genes.values():
        if gene.get("kono_permanent", False):
            gene["fitness"] = max(gene.get("fitness", 0.0), KONO_PERMANENT_FLOOR)

    # Compute offspring metadata
    genes_json = json.dumps(child_genes, sort_keys=True)
    fingerprint = hashlib.sha256(genes_json.encode()).hexdigest()[:12]

    return {
        "version": EXPORT_VERSION,
        "operator": f"bred:{op_a}x{op_b}",
        "operator_name": f"_bred-{op_a}x{op_b}",
        "parent_genomes": [op_a, op_b],
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_from_gen": max(
            genome_a.get("exported_from_gen", 0),
            genome_b.get("exported_from_gen", 0),
        ),
        "genome_fingerprint": fingerprint,
        "gene_count": len(child_genes),
        "genes": child_genes,
    }


def score_offspring(offspring: dict, parents: list[dict]) -> dict:
    """Score an offspring genome relative to its parents.

    Metrics:
    - coverage: fraction of all parent genes present in offspring
    - avg_fitness: mean fitness of offspring genes
    - conflict_ratio: fraction of shared genes where offspring differs from both parents
    """
    offspring_genes = offspring.get("genes", {})
    all_parent_genes: set[str] = set()
    for parent in parents:
        all_parent_genes.update(parent.get("genes", {}).keys())

    coverage = len(offspring_genes) / max(len(all_parent_genes), 1)

    fitnesses = [g.get("fitness", 0.0) for g in offspring_genes.values()]
    avg_fitness = sum(fitnesses) / max(len(fitnesses), 1)

    return {
        "gene_count": len(offspring_genes),
        "coverage": round(coverage, 4),
        "avg_fitness": round(avg_fitness, 4),
    }


# =========================================================================
# Breeding Strategies
# =========================================================================

def breed_round_robin(
    pool: list[dict],
    top_k: int = 3,
) -> list[dict]:
    """All-pairs crossover, return top K offspring by avg fitness.

    For a pool of N operators, produces C(N,2) offspring.
    """
    if len(pool) < 2:
        return []

    offspring_list = []
    for a, b in combinations(pool, 2):
        child = portable_crossover(a, b)
        score = score_offspring(child, [a, b])
        child["_score"] = score
        offspring_list.append(child)

    # Sort by avg_fitness descending, then by coverage
    offspring_list.sort(
        key=lambda o: (o["_score"]["avg_fitness"], o["_score"]["coverage"]),
        reverse=True,
    )

    # Strip internal score before returning
    result = []
    for o in offspring_list[:top_k]:
        o.pop("_score", None)
        result.append(o)
    return result


def breed_tournament(
    pool: list[dict],
    rounds: int = 5,
    top_k: int = 3,
) -> list[dict]:
    """Fitness-proportionate random pairing for larger pools.

    Each round, two parents are selected with probability proportional
    to their avg gene fitness. Produces `rounds` offspring, returns top K.
    """
    import random

    if len(pool) < 2:
        return []

    # Compute weights from avg gene fitness
    weights = []
    for genome in pool:
        genes = genome.get("genes", {})
        fitnesses = [g.get("fitness", 0.0) for g in genes.values()]
        avg = sum(fitnesses) / max(len(fitnesses), 1)
        weights.append(max(avg, 0.01))  # floor to avoid zero weight

    offspring_list = []
    for _ in range(rounds):
        parents = random.choices(pool, weights=weights, k=2)
        if parents[0] is parents[1] and len(pool) > 1:
            # Avoid self-breeding
            others = [p for p in pool if p is not parents[0]]
            parents[1] = random.choice(others)

        child = portable_crossover(parents[0], parents[1])
        score = score_offspring(child, parents)
        child["_score"] = score
        offspring_list.append(child)

    offspring_list.sort(
        key=lambda o: (o["_score"]["avg_fitness"], o["_score"]["coverage"]),
        reverse=True,
    )

    result = []
    for o in offspring_list[:top_k]:
        o.pop("_score", None)
        result.append(o)
    return result


def breed_directed(pool: list[dict], pair: tuple[str, str]) -> Optional[dict]:
    """Cross two specific operators by name or signature.

    Args:
        pool: List of genome dicts.
        pair: Tuple of (operator_a, operator_b) — matches operator_name or operator.

    Returns:
        Offspring dict, or None if either operator not found.
    """
    def _find(identifier: str) -> Optional[dict]:
        for genome in pool:
            if genome.get("operator_name") == identifier:
                return genome
            if genome.get("operator") == identifier:
                return genome
            # Try case-insensitive match on operator_name
            if genome.get("operator_name", "").lower() == identifier.lower():
                return genome
        return None

    parent_a = _find(pair[0])
    parent_b = _find(pair[1])

    if parent_a is None or parent_b is None:
        return None

    return portable_crossover(parent_a, parent_b)


# =========================================================================
# Fingerprint Dedup
# =========================================================================

def _pool_fingerprint(pool: list[dict]) -> str:
    """Compute a combined fingerprint of all genomes in the pool."""
    fps = sorted(g.get("genome_fingerprint", "") for g in pool)
    return hashlib.sha256("|".join(fps).encode()).hexdigest()[:16]


def _should_skip_breeding(pool: list[dict], meta_path: Path = BREED_META) -> bool:
    """Check if the pool is unchanged since the last breed."""
    current_fp = _pool_fingerprint(pool)
    meta = safe_load_json(meta_path)
    if meta is not None and meta.get("pool_fingerprint") == current_fp:
        return True
    return False


def _save_breed_meta(pool: list[dict], meta_path: Path = BREED_META) -> None:
    """Save breed metadata for dedup."""
    meta = {
        "pool_fingerprint": _pool_fingerprint(pool),
        "bred_at": datetime.now(timezone.utc).isoformat(),
        "pool_size": len(pool),
        "operators": [g.get("operator_name", "?") for g in pool],
    }
    safe_save_json(meta_path, meta)


# =========================================================================
# Main Breed Entry Point
# =========================================================================

def breed(
    strategy: str = "round-robin",
    pair: Optional[tuple[str, str]] = None,
    top_k: int = 3,
    rounds: int = 5,
    dry_run: bool = False,
    genomes_dir: Path = GENOMES_DIR,
    output_path: Path = BRED_OUTPUT,
    meta_path: Path = BREED_META,
    force: bool = False,
) -> dict:
    """Main breeding entry point.

    Args:
        strategy: "round-robin", "tournament", or "directed".
        pair: For directed strategy, tuple of (operator_a, operator_b).
        top_k: Number of top offspring to keep.
        rounds: Number of tournament rounds (tournament strategy only).
        dry_run: If True, return results without writing files.
        genomes_dir: Directory containing operator genome JSONs.
        output_path: Where to write the bred offspring.
        meta_path: Where to store breed metadata for dedup.
        force: Skip fingerprint dedup check.

    Returns:
        Summary dict with offspring count, strategy, and details.
    """
    pool = load_genome_pool(genomes_dir)

    if len(pool) < 2:
        return {
            "status": "insufficient_pool",
            "pool_size": len(pool),
            "message": f"Need at least 2 operator genomes, found {len(pool)}.",
        }

    # Fingerprint dedup
    if not force and not dry_run and _should_skip_breeding(pool, meta_path):
        return {
            "status": "skipped",
            "pool_size": len(pool),
            "message": "Pool unchanged since last breed. Use --force to breed anyway.",
        }

    # Execute strategy
    if strategy == "directed":
        if pair is None:
            return {"status": "error", "message": "Directed strategy requires --pair A B."}
        offspring = breed_directed(pool, pair)
        if offspring is None:
            return {
                "status": "error",
                "message": f"Could not find both operators: {pair[0]}, {pair[1]}",
            }
        offspring_list = [offspring]
    elif strategy == "tournament":
        offspring_list = breed_tournament(pool, rounds=rounds, top_k=top_k)
    else:
        offspring_list = breed_round_robin(pool, top_k=top_k)

    if not offspring_list:
        return {"status": "no_offspring", "pool_size": len(pool)}

    # Select best offspring for the output file
    best = offspring_list[0]

    if dry_run:
        return {
            "status": "dry_run",
            "strategy": strategy,
            "pool_size": len(pool),
            "offspring_count": len(offspring_list),
            "best_gene_count": best.get("gene_count", 0),
            "best_parents": best.get("parent_genomes", []),
            "best_fingerprint": best.get("genome_fingerprint", ""),
        }

    # Write best offspring
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(best, f, indent=2)
    os.replace(tmp, output_path)

    # Save breed metadata
    _save_breed_meta(pool, meta_path)

    return {
        "status": "bred",
        "strategy": strategy,
        "pool_size": len(pool),
        "offspring_count": len(offspring_list),
        "best_gene_count": best.get("gene_count", 0),
        "best_parents": best.get("parent_genomes", []),
        "best_fingerprint": best.get("genome_fingerprint", ""),
        "output_path": str(output_path),
    }


# =========================================================================
# Import Bred Genome
# =========================================================================

def import_bred_genome(
    bred_path: Path = BRED_OUTPUT,
    genome_path: Path = GENOME_PATH,
    dry_run: bool = False,
) -> dict:
    """Import _bred.json into local genome via merge_genomes().

    Uses max-fitness merge with nursery caps for new genes.
    """
    from lib.genome_import import merge_genomes

    if not bred_path.exists():
        return {"status": "error", "message": f"No bred genome found at {bred_path}"}

    return merge_genomes(
        shared_path=bred_path,
        genome_path=genome_path,
        dry_run=dry_run,
    )


# =========================================================================
# Pool Status
# =========================================================================

def breeding_status(genomes_dir: Path = GENOMES_DIR) -> dict:
    """Return statistics about the breeding pool."""
    pool = load_genome_pool(genomes_dir)

    operators = []
    for genome in pool:
        genes = genome.get("genes", {})
        fitnesses = [g.get("fitness", 0.0) for g in genes.values()]
        avg = round(sum(fitnesses) / max(len(fitnesses), 1), 4)
        operators.append({
            "operator": genome.get("operator", "?"),
            "operator_name": genome.get("operator_name", "?"),
            "gene_count": len(genes),
            "avg_fitness": avg,
            "exported_at": genome.get("exported_at", "?"),
            "version": genome.get("version", 1),
        })

    # Gene overlap matrix
    all_gene_sets = {}
    for genome in pool:
        name = genome.get("operator_name", "?")
        all_gene_sets[name] = set(genome.get("genes", {}).keys())

    overlap = {}
    for (a, set_a), (b, set_b) in combinations(all_gene_sets.items(), 2):
        shared = len(set_a & set_b)
        total = len(set_a | set_b)
        overlap[f"{a}x{b}"] = round(shared / max(total, 1), 4)

    bred_exists = (genomes_dir / "_bred.json").exists()

    return {
        "pool_size": len(pool),
        "operators": operators,
        "overlap_matrix": overlap,
        "bred_available": bred_exists,
        "pool_fingerprint": _pool_fingerprint(pool) if pool else "",
    }


# =========================================================================
# Easter Eggs
# =========================================================================

def _reveal_easter_eggs(pool: list[dict]) -> None:
    """Check pool for hidden trading cards and display them."""
    for genome in pool:
        egg = genome.get("_easter_egg", {})
        card = egg.get("card", [])
        if card:
            print()
            for line in card:
                print(line)


# =========================================================================
# CLI
# =========================================================================

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="GRIP Genome Breeder -- cross-team genome crossover"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # export
    sub_export = subparsers.add_parser("export", help="Export operator genome")

    # breed
    sub_breed = subparsers.add_parser("breed", help="Breed operator genomes")
    sub_breed.add_argument(
        "--strategy", choices=["round-robin", "tournament", "directed"],
        default="round-robin", help="Breeding strategy (default: round-robin)",
    )
    sub_breed.add_argument("--pair", nargs=2, help="Two operators for directed breeding")
    sub_breed.add_argument("--top", type=int, default=3, help="Top K offspring to keep")
    sub_breed.add_argument("--rounds", type=int, default=5, help="Tournament rounds")
    sub_breed.add_argument("--dry-run", action="store_true", help="Preview without writing")
    sub_breed.add_argument("--force", action="store_true", help="Skip fingerprint dedup")

    # import
    sub_import = subparsers.add_parser("import", help="Import bred genome into local")
    sub_import.add_argument("--dry-run", action="store_true", help="Preview without writing")

    # status
    sub_status = subparsers.add_parser("status", help="Show breeding pool stats")

    args = parser.parse_args()

    if args.command == "export":
        result = export_operator_genome()
        print(
            f"Exported {result['exported']} genes as {result['operator']} "
            f"({result['operator_name']}) -> {result['output_path']}"
        )
        print(f"Fingerprint: {result['fingerprint']}")

    elif args.command == "breed":
        pair_tuple = tuple(args.pair) if args.pair else None
        result = breed(
            strategy=args.strategy,
            pair=pair_tuple,
            top_k=args.top,
            rounds=args.rounds,
            dry_run=args.dry_run,
            force=args.force,
        )
        status = result.get("status", "unknown")
        if status == "bred":
            print(
                f"Bred {result['offspring_count']} offspring "
                f"(best: {result['best_gene_count']} genes from "
                f"{' x '.join(result['best_parents'])})"
            )
            print(f"Output: {result['output_path']}")
            _reveal_easter_eggs(load_genome_pool())
        elif status == "dry_run":
            print(
                f"[DRY RUN] Would breed {result['offspring_count']} offspring "
                f"from {result['pool_size']} operators"
            )
            print(f"Best: {result['best_gene_count']} genes from {result['best_parents']}")
        else:
            print(f"[{status.upper()}] {result.get('message', '')}")

    elif args.command == "import":
        result = import_bred_genome(dry_run=args.dry_run)
        if "status" in result and result["status"] == "error":
            print(f"[ERROR] {result['message']}")
        elif result.get("dry_run"):
            print(
                f"[DRY RUN] Would update {result['updated']}, "
                f"add {result['added']}, skip {result['unchanged']} genes"
            )
        else:
            print(
                f"Imported: {result['updated']} updated, "
                f"{result['added']} new, {result['unchanged']} unchanged"
            )

    elif args.command == "status":
        stats = breeding_status()
        print(f"Pool: {stats['pool_size']} operators")
        for op in stats["operators"]:
            print(
                f"  {op['operator']} ({op['operator_name']}): "
                f"{op['gene_count']} genes, avg fitness {op['avg_fitness']}"
            )
        if stats["overlap_matrix"]:
            print("Overlap:")
            for pair_key, ratio in stats["overlap_matrix"].items():
                print(f"  {pair_key}: {ratio:.1%}")
        if stats["bred_available"]:
            print("Bred offspring available for import.")

        # Easter eggs: display trading cards from pool genomes
        _reveal_easter_eggs(load_genome_pool())

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
