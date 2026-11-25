#!/usr/bin/env python3
"""genome_import.py -- Merge a shared genome into the local genome.

Merge strategy:
    Existing genes:  take max(local_fitness, imported_fitness) -- never overwrite downward
    New genes:       add with fitness capped at NEUTRAL_FITNESS (0.5) and
                     generation_born set to current generation (enters nursery period)

Sensitive fields are stripped from any new gene before insertion to ensure
no session-specific data from a foreign environment pollutes the local genome.

Usage:
    python3 lib/genome_import.py                       # merge data/genome-shared.json
    python3 lib/genome_import.py --shared PATH         # custom shared file
    python3 lib/genome_import.py --dry-run             # print plan, no write
"""

import json
import os
import sys
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
GENOME_PATH = CLAUDE_DIR / "cache" / "genome.json"
DEFAULT_SHARED = CLAUDE_DIR / "data" / "genome-shared.json"

NEUTRAL_FITNESS = 0.5

# Strip these fields from any imported gene before inserting into local genome
_IMPORT_STRIP_FIELDS = {
    "contribution_record",
    "expression_contexts",
    "kono_attestations",
    "kono_permanent",
    "ablation",
    "last_invoked",
    "invocation_count",
}


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_genome(genome: dict, path: Path) -> None:
    """Atomic write: write to .tmp then rename."""
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(genome, f, indent=2)
    os.replace(tmp, path)


def _clean_gene(gene: dict) -> dict:
    """Strip sensitive fields from an imported gene dict."""
    return {k: v for k, v in gene.items() if k not in _IMPORT_STRIP_FIELDS}


def merge_genomes(
    shared_path: Path = DEFAULT_SHARED,
    genome_path: Path = GENOME_PATH,
    dry_run: bool = False,
) -> dict:
    """Merge shared genome into local genome using max-fitness strategy.

    Args:
        shared_path: Path to the exported genome-shared.json.
        genome_path: Path to the local cache/genome.json.
        dry_run: If True, compute and return the merge plan without writing.

    Returns:
        Summary dict: {updated, added, unchanged, dry_run, plan}
        plan is a list of {name, action, old_fitness, new_fitness} entries (dry_run only).
    """
    shared = _load_json(shared_path)
    local = _load_json(genome_path)

    shared_genes: dict = shared.get("genes", {})
    local_genes: dict = local.get("genes", {})
    current_gen: int = local.get("generation", 0)

    updated = 0
    added = 0
    unchanged = 0
    plan = []

    for name, imported_gene in shared_genes.items():
        imported_fitness = imported_gene.get("fitness", 0.0)

        if name in local_genes:
            local_fitness = local_genes[name].get("fitness", 0.0)
            if imported_fitness > local_fitness:
                if dry_run:
                    plan.append({
                        "name": name,
                        "action": "update",
                        "old_fitness": local_fitness,
                        "new_fitness": imported_fitness,
                    })
                else:
                    local_genes[name]["fitness"] = imported_fitness
                updated += 1
            else:
                unchanged += 1
        else:
            # New gene: cap fitness at NEUTRAL to prevent foreign inflation gaming
            capped_fitness = min(imported_fitness, NEUTRAL_FITNESS)
            new_gene = _clean_gene(imported_gene)
            new_gene["fitness"] = capped_fitness
            new_gene["generation_born"] = current_gen
            # Ensure required fields have sensible defaults
            new_gene.setdefault("contribution_record", [])
            new_gene.setdefault("expression_contexts", [])
            new_gene.setdefault("kono_attestations", 0)
            new_gene.setdefault("kono_permanent", False)
            new_gene.setdefault("ablation", {
                "invoked_successes": 0,
                "invoked_failures": 0,
                "not_invoked_successes": 0,
                "not_invoked_failures": 0,
            })
            new_gene.setdefault("last_invoked", "")
            new_gene.setdefault("invocation_count", 0)

            if dry_run:
                plan.append({
                    "name": name,
                    "action": "add",
                    "old_fitness": None,
                    "new_fitness": capped_fitness,
                })
            else:
                local_genes[name] = new_gene
            added += 1

    if not dry_run:
        _save_genome(local, genome_path)

    result = {
        "updated": updated,
        "added": added,
        "unchanged": unchanged,
        "dry_run": dry_run,
    }
    if dry_run:
        result["plan"] = plan
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Merge shared GRIP genome into local genome")
    parser.add_argument("--shared", type=Path, default=DEFAULT_SHARED, help="Shared genome file path")
    parser.add_argument("--dry-run", action="store_true", help="Print merge plan without writing")
    args = parser.parse_args()

    result = merge_genomes(shared_path=args.shared, dry_run=args.dry_run)

    if args.dry_run:
        print(f"[DRY RUN] Would update {result['updated']}, add {result['added']}, skip {result['unchanged']} genes")
        for entry in result.get("plan", []):
            action = entry["action"]
            name = entry["name"]
            if action == "update":
                print(f"  UPDATE {name}: {entry['old_fitness']:.4f} -> {entry['new_fitness']:.4f}")
            else:
                print(f"  ADD    {name}: fitness={entry['new_fitness']:.4f} (capped at neutral)")
    else:
        print(f"Merged: {result['updated']} updated, {result['added']} new genes added, {result['unchanged']} unchanged")
