#!/usr/bin/env python3
"""Hypothesis-Genome Bridge — feeds hypothesis accuracy into genome fitness.

Connects the hypothesis engine's confirmation rate to the genome's
domain_fitness as a 'prediction_accuracy' dimension.

Fitness scoring:
- 0.0: No hypotheses registered (no data)
- 0.3: All hypotheses refuted (poor predictions but at least testing)
- 0.5: Random baseline (50/50 confirmation)
- 0.7: Good confirmation rate (60-70%, not suspiciously high)
- 0.5: >80% confirmation rate (Goodhart penalty — predictions too easy)
- Bonus: +0.1 for having >= 5 total hypotheses (scientific engagement)

The Goodhart penalty is the key insight: a system that ONLY confirms
its own predictions is not doing science — it's doing marketing.

Usage:
    PYTHONPATH=$HOME/.claude python3 lib/hypothesis_genome_bridge.py update
    PYTHONPATH=$HOME/.claude python3 lib/hypothesis_genome_bridge.py status
"""

import json
import os
import sys
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
GENOME_FILE = CLAUDE_DIR / "cache" / "genome.json"


def hypothesis_fitness(require_external: bool = True) -> dict:
    """Compute genome fitness dimension from hypothesis engine state.

    Returns:
        score: float in [0, 1]
        details: dict with breakdown
    """
    try:
        from lib.hypothesis_engine import status
        s = status()
    except (ImportError, Exception):
        return {"score": 0.0, "details": {"error": "hypothesis engine unavailable"}}

    total = s.get("total", 0)

    if require_external:
        # Only count externally-validated hypotheses
        try:
            from lib.hypothesis_engine import _load_hypotheses
            all_h = _load_hypotheses()
            confirmed = sum(1 for h in all_h
                           if h.status == "confirmed" and getattr(h, "externally_validated", False))
            refuted = sum(1 for h in all_h
                         if h.status == "refuted" and getattr(h, "externally_validated", False))
        except Exception:
            confirmed = s.get("by_status", {}).get("confirmed", 0)
            refuted = s.get("by_status", {}).get("refuted", 0)
    else:
        confirmed = s.get("by_status", {}).get("confirmed", 0)
        refuted = s.get("by_status", {}).get("refuted", 0)
    decided = confirmed + refuted

    if total == 0:
        return {"score": 0.0, "details": {"reason": "no hypotheses registered"}}

    if decided == 0:
        # All pending — credit for registering but no verdict yet
        engagement_bonus = min(total / 5, 0.1)
        return {
            "score": round(0.3 + engagement_bonus, 3),
            "details": {"reason": "all pending", "total": total},
        }

    confirmation_rate = confirmed / decided

    # Inverted-U scoring: too low = poor predictions, too high = Goodhart
    if confirmation_rate < 0.3:
        # Poor predictions but at least testing
        score = 0.3 + confirmation_rate * 0.5
    elif confirmation_rate <= 0.7:
        # Sweet spot: genuinely predictive
        score = 0.5 + (confirmation_rate - 0.3) * 0.5
    else:
        # Goodhart penalty: predictions suspiciously easy
        score = 0.7 - (confirmation_rate - 0.7) * 0.67

    # Engagement bonus: more hypotheses = more scientific engagement
    engagement_bonus = min(total / 10, 0.1)
    score = min(score + engagement_bonus, 1.0)

    return {
        "score": round(score, 3),
        "details": {
            "confirmation_rate": round(confirmation_rate, 3),
            "confirmed": confirmed,
            "refuted": refuted,
            "total": total,
            "goodhart_penalty_active": confirmation_rate > 0.7,
        },
    }


def update_genome() -> dict:
    """Update genome with prediction_accuracy dimension."""
    fitness = hypothesis_fitness()

    if not GENOME_FILE.exists():
        return {"status": "no_genome", "score": fitness["score"]}

    # #3462 bug #3: lock+atomic read-modify-write so concurrent sessions can't
    # lose the gene update (was a raw read + write_text race on the shared
    # cache/genome.json — the canonical fitness artefact).
    from genome_store import update_genome as _locked_update, LockTimeoutError

    counter = {"updated": 0}

    def _apply(genome: dict) -> dict:
        for gene in genome.get("genes", []):
            if "domain_fitness" not in gene:
                gene["domain_fitness"] = {}
            gene["domain_fitness"]["prediction_accuracy"] = fitness["score"]
            counter["updated"] += 1
        return genome

    try:
        _locked_update(GENOME_FILE, _apply)
    except (json.JSONDecodeError, OSError, LockTimeoutError) as e:
        status = "genome_unreadable" if isinstance(e, json.JSONDecodeError) else "write_failed"
        return {"status": status, "score": fitness["score"], "error": str(e)}
    updated = counter["updated"]

    return {
        "status": "updated",
        "genes_updated": updated,
        "score": fitness["score"],
        "details": fitness["details"],
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: hypothesis_genome_bridge.py <update|status>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        result = hypothesis_fitness()
        print(f"Prediction accuracy fitness: {result['score']:.3f}")
        for k, v in result["details"].items():
            print(f"  {k}: {v}")

    elif cmd == "update":
        result = update_genome()
        print(f"Genome update: {result['status']}")
        print(f"  Score: {result['score']:.3f}")
        if "genes_updated" in result:
            print(f"  Genes updated: {result['genes_updated']}")
        if "details" in result:
            for k, v in result["details"].items():
                print(f"  {k}: {v}")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
