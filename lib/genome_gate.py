#!/usr/bin/env python3
"""Genome fitness gate for skill loading.

Checks genome fitness before loading a skill. Dead genes suppress loading
to save context tokens. Protected genes always load.

Usage:
    python3 lib/genome_gate.py check <skill_name> [min_fitness]
    python3 lib/genome_gate.py batch <skill1> <skill2> ...
    python3 lib/genome_gate.py stats
"""
import json
import os
import sys
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_genome():
    """Load genome via converge_dna. Isolated for test mockability."""
    try:
        from lib.converge_dna import load_genome
        return load_genome()
    except ImportError:
        return None


def should_load_skill(skill_name: str, min_fitness: float = 0.0) -> dict:
    """Check if a skill should be loaded based on genome fitness.

    Returns: {"load": bool, "fitness": float, "tier": str, "reason": str}
    """
    genome = _load_genome()
    if genome is None:
        return {"load": True, "fitness": 0.5, "tier": "unknown", "reason": "no genome"}

    gene = genome.genes.get(skill_name)
    if gene is None:
        return {"load": True, "fitness": 0.5, "tier": "unknown", "reason": "no gene"}

    # Protected genes always load
    if gene.protected:
        return {"load": True, "fitness": gene.fitness, "tier": gene.tier, "reason": "protected"}

    # Dead genes are suppressed
    if gene.tier == "dead":
        return {"load": False, "fitness": gene.fitness, "tier": "dead", "reason": "dead gene"}

    # Fitness threshold check
    if gene.fitness < min_fitness:
        return {
            "load": False,
            "fitness": gene.fitness,
            "tier": gene.tier,
            "reason": f"below threshold {min_fitness}",
        }

    # Polyglot bonus: Level 2+ polyglot skills get fitness boost indicator
    is_polyglot = _check_polyglot(skill_name)

    return {
        "load": True,
        "fitness": gene.fitness,
        "tier": gene.tier,
        "reason": "ok",
        "polyglot": is_polyglot,
    }


def _check_polyglot(skill_name: str) -> bool:
    """Check if a skill is a Level 2+ polyglot (self-verifying)."""
    skill_path = CLAUDE_DIR / "skills" / skill_name / "SKILL.md"
    if not skill_path.exists():
        return False
    try:
        with open(skill_path, "r") as f:
            first_line = f.readline().strip()
        return first_line.startswith("#!/")
    except Exception:
        return False


def batch_check(skill_names: list, min_fitness: float = 0.0) -> dict:
    """Check multiple skills at once. Returns {name: result} dict."""
    return {name: should_load_skill(name, min_fitness) for name in skill_names}


def gate_stats() -> dict:
    """Return loading stats from genome perspective."""
    genome = _load_genome()
    if genome is None:
        return {"error": "no genome"}

    loadable = sum(1 for g in genome.genes.values() if g.tier != "dead" or g.protected)
    suppressed = sum(1 for g in genome.genes.values() if g.tier == "dead" and not g.protected)
    polyglot = sum(1 for g in genome.genes.values() if _check_polyglot(g.name))

    return {
        "total_genes": len(genome.genes),
        "loadable": loadable,
        "suppressed": suppressed,
        "polyglot_skills": polyglot,
        "savings_estimate": f"~{suppressed * 200} context tokens saved per session",
    }


def resurrect_and_sync(min_fitness: float = 0.0) -> dict:
    """Resurrect dead genes with disk files and sync genome with disk.

    Loads the genome, runs resurrect_dead_genes() then sync_genome_with_disk(),
    saves the updated genome, and returns before/after dead-gene counts.

    Returns:
        {
            "dead_before": int,
            "dead_after": int,
            "resurrected": int,
            "synced": int,
            "total_genes": int,
        }
    """
    try:
        from lib.converge_dna import (
            load_genome,
            save_genome,
            resurrect_dead_genes,
            sync_genome_with_disk,
        )
        from lib.converge_dna_core import DEAD_THRESHOLD
    except ImportError as exc:
        return {"error": str(exc)}

    genome = load_genome()
    dead_before = sum(1 for g in genome.genes.values() if g.fitness <= DEAD_THRESHOLD and not g.protected)

    resurrected = resurrect_dead_genes(genome)
    sync_result = sync_genome_with_disk(genome)

    save_genome(genome, reason="gate")

    dead_after = sum(1 for g in genome.genes.values() if g.fitness <= DEAD_THRESHOLD and not g.protected)

    return {
        "dead_before": dead_before,
        "dead_after": dead_after,
        "resurrected": resurrected,
        "synced": sync_result.get("count", 0),
        "total_genes": len(genome.genes),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: genome_gate.py check|batch|stats [args]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "check":
        name = sys.argv[2] if len(sys.argv) > 2 else ""
        min_fit = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
        print(json.dumps(should_load_skill(name, min_fit)))
    elif cmd == "batch":
        names = sys.argv[2:]
        print(json.dumps(batch_check(names)))
    elif cmd == "stats":
        print(json.dumps(gate_stats(), indent=2))
    elif cmd == "resurrect":
        print(json.dumps(resurrect_and_sync(), indent=2))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
