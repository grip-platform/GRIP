#!/usr/bin/env python3
"""evolve.py — Unified Evolution Dispatcher.

Thin orchestration layer that routes EvolutionState between GRIP's four
learning engines: /evo (observe), /learn (detect), converge_metrics (measure),
and converge_dna (evolve genome). Closes 5 documented open feedback loops.

This is a ROUTER, not a monolith. Each phase calls existing lib/ modules.
Individual commands (/evo, /learn, /converge, /rsi) remain standalone.

Usage:
    python3 lib/evolve.py                    # Full pipeline (all phases)
    python3 lib/evolve.py observe            # Phase 1 only (/evo)
    python3 lib/evolve.py learn              # Phase 2 only (/learn)
    python3 lib/evolve.py measure            # Phase 3 only (metrics + DELIGHT)
    python3 lib/evolve.py genome             # Phase 4 only (evolve genome)
    python3 lib/evolve.py propose            # Phase 5 only (genesis proposals)
    python3 lib/evolve.py closeout           # RSI closeout sequence
"""

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
EVOLVE_LOG = CLAUDE_DIR / "learning" / "evolve_runs.jsonl"

# Single path registration — all lib/ modules importable from here down.
# Phases accept injectable callables for testability (DIP compliance).
if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
    sys.path.insert(0, str(Path(__file__).resolve().parent))


@dataclass
class EvolutionState:
    """Shared state flowing through all /evolve phases.

    Each phase reads what it needs and writes its output.
    The state is the bus — phases are the passengers.
    """

    # Metadata
    run_id: str = ""
    timestamp: str = ""
    phases_run: list = field(default_factory=list)

    # Phase 1: OBSERVE (/evo)
    evo_ran: bool = False
    calibrated_fd: float = 0.0
    ff_rate: float = 0.0
    facet_count: int = 0
    genesis_proposals: list = field(default_factory=list)
    evo_confidence: float = 0.0

    # Phase 2: LEARN
    learn_ran: bool = False
    insights_detected: int = 0
    candidates_approved: int = 0
    rules_generated: int = 0

    # Phase 3: MEASURE
    measure_ran: bool = False
    delight_composite: float = 0.0
    converge_efficiency: float = 0.0
    converge_runs_total: int = 0

    # Phase 4: EVOLVE GENOME
    genome_evolved: bool = False
    genome_generation: int = 0
    genome_gene_count: int = 0
    genome_avg_fitness: float = 0.0
    fitness_dimensions_added: list = field(default_factory=list)

    # Phase 5: PROPOSE
    proposals_applied: int = 0
    skills_created: list = field(default_factory=list)


def _generate_run_id() -> str:
    """Short deterministic run ID."""
    return f"evolve-{int(time.time()) % 100000}"


# =========================================================================
# Phase 1: OBSERVE — calibrate friction via /evo
# =========================================================================

def phase_observe(state: EvolutionState, evo_runner=None) -> EvolutionState:
    """Run evo calibration and capture results."""
    try:
        if evo_runner is None:
            from evo import run as evo_runner
        evo_state = evo_runner(max_depth=5)
        if evo_state:
            state.evo_ran = True
            state.calibrated_fd = evo_state.calibrated_friction_density
            state.ff_rate = evo_state.false_flag_rate
            state.facet_count = len(evo_state.verdicts)
            state.genesis_proposals = evo_state.genome_proposals
            state.evo_confidence = evo_state.confidence
        else:
            state.evo_ran = False
    except Exception as e:
        logger.warning(f"Phase 1 (observe) failed: {e}")
        state.evo_ran = False
    state.phases_run.append("observe")
    return state


# =========================================================================
# Phase 2: LEARN — detect patterns
# =========================================================================

def phase_learn(state: EvolutionState) -> EvolutionState:
    """Run learning detection pipeline."""
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, str(CLAUDE_DIR / "lib" / "learning_detector.py"), "consolidate"],
            capture_output=True, text=True, timeout=60,
            cwd=str(CLAUDE_DIR),
        )
        state.learn_ran = result.returncode == 0
        # Parse output for counts if available
        for line in result.stdout.splitlines():
            if "insights" in line.lower() and any(c.isdigit() for c in line):
                nums = [int(s) for s in line.split() if s.isdigit()]
                if nums:
                    state.insights_detected = nums[0]
    except Exception as e:
        logger.warning(f"Phase 2 (learn) failed: {e}")
        state.learn_ran = False
    state.phases_run.append("learn")
    return state


# =========================================================================
# Phase 3: MEASURE — DELIGHT + converge metrics
# =========================================================================

def phase_measure(state: EvolutionState) -> EvolutionState:
    """Capture DELIGHT snapshot and converge compound analysis."""
    try:
        # Read latest DELIGHT
        delight_file = CLAUDE_DIR / "learning" / "delight_v2.jsonl"
        if delight_file.exists():
            lines = delight_file.read_text().strip().splitlines()
            if lines:
                last = json.loads(lines[-1])
                state.delight_composite = last.get("composite", 0.0)

        # Read converge metrics summary
        metrics_file = CLAUDE_DIR / "learning" / "converge-metrics.jsonl"
        if metrics_file.exists():
            lines = metrics_file.read_text().strip().splitlines()
            state.converge_runs_total = len(lines)
            if lines:
                last = json.loads(lines[-1])
                state.converge_efficiency = last.get("convergence_efficiency", 0.0)

        state.measure_ran = True
    except Exception as e:
        logger.warning(f"Phase 3 (measure) failed: {e}")
        state.measure_ran = False
    state.phases_run.append("measure")
    return state


# =========================================================================
# Phase 4: EVOLVE GENOME — unified fitness update
# =========================================================================

def _apply_fitness_dimension(genome, name: str, score: float) -> None:
    """Set a domain fitness score on all genes that support it."""
    for gene in genome.genes.values():
        if hasattr(gene, 'domain_fitness') and gene.domain_fitness:
            gene.domain_fitness.domain_scores.setdefault(name, score)


def _load_hypothesis_rate() -> float | None:
    """Load hypothesis confirmation rate from JSONL. Returns None if insufficient data."""
    hyp_file = CLAUDE_DIR / "learning" / "hypotheses.jsonl"
    if not hyp_file.exists():
        return None
    hyp_lines = hyp_file.read_text().strip().splitlines()
    confirmed = sum(1 for line in hyp_lines if '"confirmed"' in line)
    refuted = sum(1 for line in hyp_lines if '"refuted"' in line)
    total = confirmed + refuted
    return confirmed / total if total >= 2 else None


def phase_genome(state: EvolutionState, genome_loader=None, genome_saver=None) -> EvolutionState:
    """Evolve genome with multi-phase fitness signals."""
    try:
        if genome_loader is None:
            from converge_dna import load_genome as genome_loader
        if genome_saver is None:
            from converge_dna import save_genome as genome_saver

        genome = genome_loader()
        if not genome:
            state.genome_evolved = False
            state.phases_run.append("genome")
            return state

        dims_added = []
        signals = [
            ("delight", state.delight_composite),
            ("evo_calibration", state.evo_confidence),
            ("converge_efficiency", state.converge_efficiency),
        ]
        for name, score in signals:
            if score > 0:
                _apply_fitness_dimension(genome, name, score)
                dims_added.append(name)

        # Hypothesis confirmation rate (Loop 5)
        try:
            conf_rate = _load_hypothesis_rate()
            if conf_rate is not None:
                _apply_fitness_dimension(genome, "hypothesis_accuracy", conf_rate)
                dims_added.append(f"hypothesis_accuracy({conf_rate:.0%})")
        except Exception:
            pass

        genome_saver(genome)
        state.genome_evolved = True
        state.genome_generation = genome.generation
        state.genome_gene_count = len(genome.genes)
        state.genome_avg_fitness = genome.avg_fitness
        state.fitness_dimensions_added = dims_added
    except Exception as e:
        logger.warning(f"Phase 4 (genome) failed: {e}")
        state.genome_evolved = False
    state.phases_run.append("genome")
    return state


# =========================================================================
# Phase 5: PROPOSE — apply genesis proposals
# =========================================================================

def phase_propose(state: EvolutionState, genome_loader=None, genome_saver=None, skill_proposer=None) -> EvolutionState:
    """Apply pending genesis proposals as micro-skills."""
    try:
        proposals_file = CLAUDE_DIR / "learning" / "pending" / "evo-proposals.json"
        if not proposals_file.exists():
            state.phases_run.append("propose")
            return state

        proposals = json.loads(proposals_file.read_text())
        if not proposals:
            state.phases_run.append("propose")
            return state

        if genome_loader is None:
            from converge_dna import load_genome as genome_loader
        if genome_saver is None:
            from converge_dna import save_genome as genome_saver
        if skill_proposer is None:
            from converge_dna import propose_micro_skill as skill_proposer

        genome = genome_loader()
        if not genome:
            state.phases_run.append("propose")
            return state

        created = []
        for proposal in proposals:
            genome, path = skill_proposer(proposal, genome)
            if path:
                created.append(str(path))

        if created:
            genome_saver(genome)
            # Clear consumed proposals
            proposals_file.write_text("[]")

        state.proposals_applied = len(created)
        state.skills_created = created
    except Exception as e:
        logger.warning(f"Phase 5 (propose) failed: {e}")
    state.phases_run.append("propose")
    return state


# =========================================================================
# Orchestration
# =========================================================================

PHASES = {
    "observe": phase_observe,
    "learn": phase_learn,
    "measure": phase_measure,
    "genome": phase_genome,
    "propose": phase_propose,
}

ALL_PHASES = ["observe", "learn", "measure", "genome", "propose"]


def run(phases: list[str] | None = None, quiet: bool = False) -> EvolutionState:
    """Run the evolution pipeline.

    Args:
        phases: List of phase names to run. None = all phases.
        quiet: Suppress output.

    Returns:
        EvolutionState with all phase outputs.
    """
    state = EvolutionState(
        run_id=_generate_run_id(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    to_run = phases or ALL_PHASES
    start = time.time()

    for phase_name in to_run:
        phase_fn = PHASES.get(phase_name)
        if not phase_fn:
            logger.warning(f"Unknown phase: {phase_name}")
            continue
        state = phase_fn(state)

    elapsed = time.time() - start

    # Record the run
    _record_run(state, elapsed)

    if not quiet:
        _print_report(state, elapsed)

    return state


def run_closeout() -> EvolutionState:
    """RSI closeout sequence — replaces subprocess chain."""
    return run(phases=["observe", "learn", "measure", "genome", "propose"], quiet=False)


def _record_run(state: EvolutionState, elapsed: float) -> None:
    """Append run to evolve_runs.jsonl."""
    entry = {
        "run_id": state.run_id,
        "timestamp": state.timestamp,
        "phases": state.phases_run,
        "elapsed_sec": round(elapsed, 2),
        "evo_ran": state.evo_ran,
        "ff_rate": state.ff_rate,
        "delight": state.delight_composite,
        "genome_gen": state.genome_generation,
        "genome_genes": state.genome_gene_count,
        "proposals_applied": state.proposals_applied,
    }
    EVOLVE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(EVOLVE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _print_report(state: EvolutionState, elapsed: float) -> None:
    """Print the evolution report."""
    print()
    print("================================================================")
    print("  /evolve — Unified Evolution Report")
    print("================================================================")
    print()
    print(f"  Run:     {state.run_id}")
    print(f"  Phases:  {', '.join(state.phases_run)}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print()

    if state.evo_ran:
        print(f"  Phase 1 OBSERVE:  {state.facet_count} facets, FF={state.ff_rate:.1%}, "
              f"conf={state.evo_confidence:.2f}")
        if state.genesis_proposals:
            print(f"    Proposals: {len(state.genesis_proposals)}")

    if state.learn_ran:
        print(f"  Phase 2 LEARN:    {state.insights_detected} insights detected")

    if state.measure_ran:
        print(f"  Phase 3 MEASURE:  DELIGHT={state.delight_composite:.3f}, "
              f"converge_eff={state.converge_efficiency:.2f}, "
              f"runs={state.converge_runs_total}")

    if state.genome_evolved:
        print(f"  Phase 4 GENOME:   gen {state.genome_generation}, "
              f"{state.genome_gene_count} genes, avg_fit={state.genome_avg_fitness:.3f}")
        if state.fitness_dimensions_added:
            print(f"    New dims: {', '.join(state.fitness_dimensions_added)}")

    if state.proposals_applied > 0:
        print(f"  Phase 5 PROPOSE:  {state.proposals_applied} skills created")
        for s in state.skills_created:
            print(f"    {s}")

    print()
    print("================================================================")
    print()


# =========================================================================
# CLI
# =========================================================================

def main():
    args = sys.argv[1:]

    if not args or args == ["all"]:
        run()
    elif args == ["closeout"]:
        run_closeout()
    else:
        # Support multiple phase args: evolve.py measure genome propose
        phases = [a for a in args if a in PHASES]
        unknown = [a for a in args if a not in PHASES]
        if unknown:
            print(f"Unknown phase(s): {', '.join(unknown)}")
            print(f"Available: {', '.join(ALL_PHASES)} | all | closeout")
            sys.exit(1)
        run(phases=phases)


if __name__ == "__main__":
    main()
