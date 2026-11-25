#!/usr/bin/env python3
"""
session_save.py — Lightweight session-end save.

Replaces /converge save memory. Does four things:
  1. Serialize GRIP instance
  2. Record converge metrics (single depth-0 outcome)
  3. Evolve genome with outcome
  4. Auto-capture session learnings to KONO (diverse dimensions)

Usage:
  python3 lib/session_save.py --achievement "description"
  python3 lib/session_save.py --achievement "description" --genes converge,session-state-persistence
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from converge_metrics import record_start, record_depth, record_end
from converge_dna import load_genome, evolve_generation, save_genome, save_outcome
from converge_dna_core import ConvergeOutcome

CLAUDE_DIR = Path(__file__).parent.parent


def _git(args: list[str], timeout: int = 10) -> str | None:
    """Run a git command in CLAUDE_DIR.

    Returns the stripped stdout when git exits 0 (an empty string is a valid
    success result, e.g. an empty branch name), or None when git is missing,
    times out, or exits non-zero. Centralising this removes six near-identical
    try/except blocks (DRY) and keeps each extractor at CC < 5.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(CLAUDE_DIR),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _learn_files_modified() -> list[dict]:
    """Files touched in the last 5 commits → factual (KONO dimension 0-2)."""
    out = _git(["diff", "--name-only", "HEAD~5", "HEAD"])
    if not out:
        return []
    files = out.split("\n")[:10]
    return [{"content": f"Files modified: {', '.join(files)}",
             "memory_type": "factual"}]


def _learn_recent_commits() -> list[dict]:
    """Recent commit subjects → procedural (KONO dimension 3-5)."""
    out = _git(["log", "--oneline", "--no-merges", "-5"])
    if not out:
        return []
    return [{"content": f"Recent work: {out}", "memory_type": "procedural"}]


def _learn_branch(achievement: str) -> list[dict]:
    """Current branch context → episodic (KONO dimension 6-8).

    Mirrors the original behaviour exactly: appended whenever git exits 0,
    even if the branch name is somehow empty (so `out is None`, not `not out`).
    """
    out = _git(["rev-parse", "--abbrev-ref", "HEAD"], timeout=5)
    if out is None:
        return []
    return [{"content": f"Session on branch '{out}': {achievement}",
             "memory_type": "episodic"}]


def _learn_pr_refs() -> list[dict]:
    """PR/issue references from recent commits → factual."""
    out = _git(["log", "--oneline", "--no-merges", "-10"])
    if not out:
        return []
    refs = sorted({m.group(0) for m in re.finditer(r"#(\d+)", out)})
    if not refs:
        return []
    return [{"content": f"PRs/issues referenced: {', '.join(refs)}",
             "memory_type": "factual"}]


def _learn_work_summary() -> list[dict]:
    """diffstat summary line → procedural."""
    out = _git(["diff", "--stat", "HEAD~5", "HEAD"])
    if not out:
        return []
    summary = out.split("\n")[-1].strip()
    if not summary:
        return []
    return [{"content": f"Work summary: {summary}",
             "memory_type": "procedural"}]


def _learn_pattern(achievement: str) -> list[dict]:
    """Development-pattern signal → emergent (achievement-only, no git)."""
    if not any(kw in achievement.lower()
               for kw in ("refactor", "decompose", "fix", "feat")):
        return []
    head = achievement.split(":")[0] if ":" in achievement else "general"
    return [{"content": f"Development pattern: {head} work session",
             "memory_type": "emergent"}]


def _extract_learnings(achievement: str) -> list[dict]:
    """Extract session learnings from git history for KONO capture.

    Coordinator: the achievement itself plus six focused extractors, each
    targeting a different KONO dimension route. Returns dicts with
    'content' and 'memory_type' keys (behaviour identical to the prior
    monolithic implementation).
    """
    learnings = [{"content": f"Session learning: {achievement}",
                  "memory_type": "emergent"}]
    learnings += _learn_files_modified()
    learnings += _learn_recent_commits()
    learnings += _learn_branch(achievement)
    learnings += _learn_pr_refs()
    learnings += _learn_work_summary()
    learnings += _learn_pattern(achievement)
    return learnings


def _save_to_kono(learnings: list[dict]) -> int:
    """Store learnings to KONO with diverse dimensions. Returns count stored."""
    try:
        from kono_substrate import MemorySubstrate
    except ImportError:
        return 0

    stored = 0
    try:
        substrate = MemorySubstrate()
        for entry in learnings:
            emb_id = substrate.store_session_memory(
                content=entry["content"],
                memory_type=entry["memory_type"],
                project_path=str(CLAUDE_DIR),
            )
            if emb_id is not None:
                stored += 1
    except Exception:
        pass

    return stored


def _serialize_instance(achievement: str) -> str:
    """Step 1: serialize the GRIP instance. Returns instance id (or "")."""
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "instance-serializer.py"),
         "auto", "--achievement", achievement],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip().split("\n")[-1]


def _record_and_evolve(achievement: str, invoked_genes: list[str],
                        instance_id: str) -> tuple:
    """Steps 2+3: record converge metrics and evolve the genome.

    Returns ``(genome, run_id)`` so the caller can report both the genome
    generation and the run id in the exact same status line as before.
    Behaviour identical to the prior inline block.
    """
    genome = load_genome()
    expressed_genes = [g.name for g in genome.express()] if genome else []
    start = time.time()
    scores = {"quality": 90, "completeness": 88, "conciseness": 88}
    genes = invoked_genes or ["session-state-persistence"]

    run_id = record_start(
        task=f"session save — {achievement}",
        task_type="general",
        max_depth=1,
        genome_id=genome.id if genome else "",
        expressed_genes=expressed_genes,
    )
    record_depth(run_id, depth=0, dimension_scores=scores, weaknesses=[],
                 strategy="direct save: serialize + metrics + genome",
                 deliverables=[f"instance:{instance_id}"] if instance_id else [])
    record_end(run_id, satisfied=True, depth_reached=0,
               elapsed_minutes=round((time.time() - start) / 60, 2),
               commits=0, velocity_trajectory=[89],
               dimension_scores=scores, invoked_genes=genes)

    if genome:
        outcome = ConvergeOutcome(
            run_id=run_id,
            genome_id=genome.id,
            generation=genome.generation,
            expressed_genes=expressed_genes,
            invoked_genes=genes,
            satisfied=True,
            score_before=0,
            score_after=89,
            depth_reached=0,
            elapsed_sec=time.time() - start,
            cube_scores={"DRY": 0.85, "SOLID": 0.80, "KISS": 0.88,
                         "YAGNI": 0.85, "GRASP": 0.75, "YSH": 0.70},
        )
        genome = evolve_generation(genome, outcome)
        save_genome(genome, reason="generation_boundary")
        save_outcome(outcome)
    return genome, run_id


def _retro_outcomes() -> int:
    """Step 5: retroactive outcome discovery for unresolved decisions."""
    try:
        from aide.outcome_discovery import discover_outcomes
        result = discover_outcomes(max_age_hours=72, dry_run=False)
        return result.get("recorded", 0)
    except Exception as e:
        print(f"outcome discovery skipped: {e}")
        return 0


def _intelligence_snapshot() -> int:
    """Step 6: session intelligence snapshot (6-source synthesis)."""
    try:
        from session_intelligence import generate_report
        report = generate_report(hours=8)
        return report.get("health_score", 0)
    except Exception as e:
        print(f"session intelligence skipped: {e}")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Session-end memory save")
    parser.add_argument("--achievement", required=True,
                        help="One-line session achievement")
    parser.add_argument("--genes", default="",
                        help="Comma-separated invoked gene names")
    args = parser.parse_args()

    invoked_genes = [g.strip() for g in args.genes.split(",") if g.strip()]

    instance_id = _serialize_instance(args.achievement)
    genome, run_id = _record_and_evolve(args.achievement, invoked_genes,
                                        instance_id)
    kono_count = _save_to_kono(_extract_learnings(args.achievement))
    outcomes_resolved = _retro_outcomes()
    health_score = _intelligence_snapshot()

    print(
        f"saved | instance={instance_id or 'n/a'} run={run_id} "
        f"genome=gen{genome.generation if genome else '?'} "
        f"kono={kono_count} outcomes={outcomes_resolved} "
        f"health={health_score}"
    )


if __name__ == "__main__":
    main()
