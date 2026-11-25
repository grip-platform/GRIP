#!/usr/bin/env python3
"""
learning_extractor.py -- Extract actionable patterns from converge run history.

Reads learning/converge-metrics.jsonl and detects recurring patterns across
converge runs: winning strategies, stall patterns, gene synergies/anti-patterns,
depth limits, and time-of-day effects.

Usage:
    python3 lib/learning_extractor.py extract
    python3 lib/learning_extractor.py extract --min-confidence 0.8
    python3 lib/learning_extractor.py extract --type strategy
    python3 lib/learning_extractor.py extract --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Optional

METRICS_PATH = Path(__file__).resolve().parent.parent / "learning" / "converge-metrics.jsonl"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Learning:
    pattern: str
    pattern_type: str  # strategy, stall, gene_combo, gene_anti, depth_limit, time
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    frequency: int = 0
    suggested_action: str = ""


@dataclass
class LearningReport:
    learnings: list[Learning] = field(default_factory=list)
    total_runs_analyzed: int = 0
    patterns_found: int = 0


# ---------------------------------------------------------------------------
# Run loading
# ---------------------------------------------------------------------------

def load_and_group_runs(metrics_path: str | Path = METRICS_PATH) -> list[dict]:
    """Load JSONL events and merge them into complete run records.

    Each run is built by joining converge_start, converge_depth (all depths),
    and converge_end events on run_id.
    """
    metrics_path = Path(metrics_path)
    if not metrics_path.exists():
        return []

    starts: dict[str, dict] = {}
    depths: dict[str, list[dict]] = defaultdict(list)
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
            if event == "converge_start":
                starts[run_id] = rec
            elif event == "converge_depth":
                depths[run_id].append(rec)
            elif event == "converge_end":
                ends[run_id] = rec

    runs: list[dict] = []
    for run_id, start in starts.items():
        end = ends.get(run_id)
        depth_events = sorted(depths.get(run_id, []), key=lambda d: d.get("depth", 0))

        run: dict = {
            "run_id": run_id,
            "task": start.get("task", ""),
            "task_type": start.get("task_type", ""),
            "max_depth": start.get("max_depth", 3),
            "timestamp": start.get("timestamp", ""),
            "expressed_genes": start.get("expressed_genes", []),
            "depths": depth_events,
        }

        if end:
            run["final_score"] = end.get("final_score", 0.0)
            run["satisfied"] = end.get("satisfied", False)
            run["depth_reached"] = end.get("depth_reached", 0)
            run["elapsed_minutes"] = end.get("elapsed_minutes", 0.0)
            run["velocity_trajectory"] = end.get("velocity_trajectory", [])
            run["invoked_genes"] = end.get("invoked_genes", [])
            run["commits"] = end.get("commits", 0)
        else:
            # Incomplete run -- mark as unsatisfied
            run["final_score"] = 0.0
            run["satisfied"] = False
            run["depth_reached"] = len(depth_events)
            run["elapsed_minutes"] = 0.0
            run["velocity_trajectory"] = []
            run["invoked_genes"] = []
            run["commits"] = 0

        runs.append(run)

    return runs


# ---------------------------------------------------------------------------
# Pattern detectors
# ---------------------------------------------------------------------------

def _extract_strategies(runs: list[dict]) -> dict[str, list[dict]]:
    """Group runs by strategy strings found at depth events."""
    strategy_runs: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        seen: set[str] = set()
        for dep in run.get("depths", []):
            strategy = dep.get("strategy", "").strip()
            if strategy and strategy not in seen:
                seen.add(strategy)
                strategy_runs[strategy].append(run)
    return strategy_runs


def detect_winning_strategies(runs: list[dict]) -> list[Learning]:
    """Strategies that appear in 3+ successful runs (satisfied=True, score >= 80)."""
    strategy_runs = _extract_strategies(runs)
    learnings: list[Learning] = []

    for strategy, strat_runs in strategy_runs.items():
        successful = [r for r in strat_runs if r.get("satisfied") and r.get("final_score", 0) >= 80]
        if len(successful) < 3:
            continue
        success_rate = len(successful) / len(strat_runs) if strat_runs else 0.0
        learnings.append(Learning(
            pattern=f"Strategy '{strategy}' succeeds consistently",
            pattern_type="strategy",
            evidence=[r["run_id"] for r in successful],
            confidence=round(success_rate, 3),
            frequency=len(successful),
            suggested_action=f"Prioritize this strategy for similar tasks",
        ))

    return sorted(learnings, key=lambda l: l.confidence, reverse=True)


def detect_stall_patterns(runs: list[dict]) -> list[Learning]:
    """Task types that consistently stall at specific depths (velocity <= 0)."""
    # Group by task_type -> depth -> velocity observations
    type_depth_velocities: dict[str, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))

    for run in runs:
        task_type = run.get("task_type", "general")
        trajectory = run.get("velocity_trajectory", [])
        for i in range(1, len(trajectory)):
            velocity = trajectory[i] - trajectory[i - 1]
            if velocity <= 0:
                type_depth_velocities[task_type][i].append(run)

    learnings: list[Learning] = []
    for task_type, depth_map in type_depth_velocities.items():
        for depth, stalled_runs in depth_map.items():
            if len(stalled_runs) < 3:
                continue
            # Confidence = fraction of runs of this type that stall at this depth
            total_runs_of_type = [r for r in runs if r.get("task_type") == task_type]
            confidence = len(stalled_runs) / len(total_runs_of_type) if total_runs_of_type else 0.0
            learnings.append(Learning(
                pattern=f"Task type '{task_type}' stalls at depth {depth}",
                pattern_type="stall",
                evidence=[r["run_id"] for r in stalled_runs],
                confidence=round(confidence, 3),
                frequency=len(stalled_runs),
                suggested_action=f"Try a different approach at depth {depth} for '{task_type}' tasks",
            ))

    return sorted(learnings, key=lambda l: l.confidence, reverse=True)


def detect_gene_synergies(runs: list[dict]) -> list[Learning]:
    """Gene combinations that appear together in 3+ successful runs."""
    # Collect gene sets from successful runs
    successful_gene_sets: list[tuple[set[str], dict]] = []
    for run in runs:
        if run.get("satisfied") and run.get("final_score", 0) >= 80:
            genes = set(run.get("invoked_genes", []))
            if len(genes) >= 2:
                successful_gene_sets.append((genes, run))

    # Count co-occurrence of all gene pairs
    pair_success: dict[tuple[str, str], list[str]] = defaultdict(list)
    pair_total: dict[tuple[str, str], int] = defaultdict(int)

    # Count pairs in ALL runs with gene data
    for run in runs:
        genes = sorted(set(run.get("invoked_genes", [])))
        if len(genes) < 2:
            continue
        for a, b in combinations(genes, 2):
            pair_total[(a, b)] += 1

    # Count pairs in successful runs
    for genes, run in successful_gene_sets:
        for a, b in combinations(sorted(genes), 2):
            pair_success[(a, b)].append(run["run_id"])

    learnings: list[Learning] = []
    for pair, run_ids in pair_success.items():
        if len(run_ids) < 3:
            continue
        total = pair_total.get(pair, len(run_ids))
        confidence = len(run_ids) / total if total > 0 else 0.0
        learnings.append(Learning(
            pattern=f"Gene synergy: '{pair[0]}' + '{pair[1]}' succeed together",
            pattern_type="gene_combo",
            evidence=run_ids,
            confidence=round(confidence, 3),
            frequency=len(run_ids),
            suggested_action=f"Co-express '{pair[0]}' and '{pair[1]}' for higher success rate",
        ))

    return sorted(learnings, key=lambda l: l.confidence, reverse=True)


def detect_gene_anti_patterns(runs: list[dict]) -> list[Learning]:
    """Genes that appear in 3+ failed runs (satisfied=False or score < 60)."""
    gene_failures: dict[str, list[str]] = defaultdict(list)
    gene_total: dict[str, int] = defaultdict(int)

    for run in runs:
        genes = run.get("invoked_genes", [])
        if not genes:
            continue
        for g in genes:
            gene_total[g] += 1
        failed = not run.get("satisfied") or run.get("final_score", 0) < 60
        if failed:
            for g in genes:
                gene_failures[g].append(run["run_id"])

    learnings: list[Learning] = []
    for gene, run_ids in gene_failures.items():
        if len(run_ids) < 3:
            continue
        total = gene_total.get(gene, len(run_ids))
        failure_rate = len(run_ids) / total if total > 0 else 0.0
        learnings.append(Learning(
            pattern=f"Gene '{gene}' appears in multiple failed runs",
            pattern_type="gene_anti",
            evidence=run_ids,
            confidence=round(failure_rate, 3),
            frequency=len(run_ids),
            suggested_action=f"Consider removing '{gene}' from default expression",
        ))

    return sorted(learnings, key=lambda l: l.confidence, reverse=True)


def detect_depth_limits(runs: list[dict]) -> list[Learning]:
    """Task types where going beyond depth N never improves score."""
    # Group by task_type
    type_runs: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        task_type = run.get("task_type", "general")
        type_runs[task_type].append(run)

    learnings: list[Learning] = []
    for task_type, task_runs in type_runs.items():
        if len(task_runs) < 3:
            continue

        # For each potential depth cap N, check if ALL runs past depth N have velocity <= 0
        max_depth_seen = max(r.get("depth_reached", 0) for r in task_runs)
        for cap_depth in range(1, max_depth_seen):
            runs_past_cap = []
            all_stalled = True
            for run in task_runs:
                trajectory = run.get("velocity_trajectory", [])
                if len(trajectory) <= cap_depth:
                    continue
                runs_past_cap.append(run)
                # Check all steps past cap_depth
                for i in range(cap_depth, len(trajectory) - 1):
                    if trajectory[i + 1] - trajectory[i] > 0:
                        all_stalled = False
                        break
                if not all_stalled:
                    break

            if all_stalled and len(runs_past_cap) >= 3:
                learnings.append(Learning(
                    pattern=f"Task type '{task_type}' gains nothing beyond depth {cap_depth}",
                    pattern_type="depth_limit",
                    evidence=[r["run_id"] for r in runs_past_cap],
                    confidence=round(1.0, 3),  # All runs past cap stalled
                    frequency=len(runs_past_cap),
                    suggested_action=f"Cap depth at {cap_depth} for '{task_type}' tasks",
                ))

    return sorted(learnings, key=lambda l: l.confidence, reverse=True)


def detect_time_patterns(runs: list[dict]) -> list[Learning]:
    """Hours of day where success rate deviates >20% from average."""
    hour_outcomes: dict[int, list[bool]] = defaultdict(list)

    for run in runs:
        ts = run.get("timestamp", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
            hour = dt.hour
        except (ValueError, TypeError):
            continue
        succeeded = run.get("satisfied", False) and run.get("final_score", 0) >= 80
        hour_outcomes[hour].append(succeeded)

    # Compute overall success rate
    all_outcomes = [o for outcomes in hour_outcomes.values() for o in outcomes]
    if not all_outcomes:
        return []
    avg_success = sum(all_outcomes) / len(all_outcomes)
    if avg_success == 0:
        return []

    learnings: list[Learning] = []
    for hour, outcomes in sorted(hour_outcomes.items()):
        if len(outcomes) < 3:
            continue
        hour_rate = sum(outcomes) / len(outcomes)
        deviation = (hour_rate - avg_success) / avg_success
        if abs(deviation) < 0.2:
            continue

        direction = "higher" if deviation > 0 else "lower"
        learnings.append(Learning(
            pattern=f"Success rate at hour {hour:02d} is {abs(deviation)*100:.0f}% {direction} than average",
            pattern_type="time",
            evidence=[],  # No specific run_ids for aggregate patterns
            confidence=round(abs(deviation), 3),
            frequency=len(outcomes),
            suggested_action=f"{'Prefer' if deviation > 0 else 'Avoid'} scheduling converge runs at hour {hour:02d}",
        ))

    return sorted(learnings, key=lambda l: l.confidence, reverse=True)


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract_learnings(
    metrics_path: str | Path = METRICS_PATH,
    min_confidence: float = 0.5,
) -> LearningReport:
    """Run all detectors, filter by confidence, return report."""
    runs = load_and_group_runs(metrics_path)
    if not runs:
        return LearningReport(learnings=[], total_runs_analyzed=0, patterns_found=0)

    all_learnings: list[Learning] = []
    all_learnings.extend(detect_winning_strategies(runs))
    all_learnings.extend(detect_stall_patterns(runs))
    all_learnings.extend(detect_gene_synergies(runs))
    all_learnings.extend(detect_gene_anti_patterns(runs))
    all_learnings.extend(detect_depth_limits(runs))
    all_learnings.extend(detect_time_patterns(runs))

    filtered = [l for l in all_learnings if l.confidence >= min_confidence]
    filtered.sort(key=lambda l: l.confidence, reverse=True)

    return LearningReport(
        learnings=filtered,
        total_runs_analyzed=len(runs),
        patterns_found=len(filtered),
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

TYPE_LABELS = {
    "strategy": "WINNING STRATEGY",
    "stall": "STALL PATTERN",
    "gene_combo": "GENE SYNERGY",
    "gene_anti": "GENE ANTI-PATTERN",
    "depth_limit": "DEPTH LIMIT",
    "time": "TIME PATTERN",
}


def format_report(report: LearningReport) -> str:
    """Format a LearningReport as human-readable text."""
    lines: list[str] = []
    lines.append(f"Learning Extractor Report")
    lines.append(f"========================")
    lines.append(f"Runs analyzed: {report.total_runs_analyzed}")
    lines.append(f"Patterns found: {report.patterns_found}")
    lines.append("")

    if not report.learnings:
        lines.append("No patterns detected above confidence threshold.")
        return "\n".join(lines)

    for i, learning in enumerate(report.learnings, 1):
        label = TYPE_LABELS.get(learning.pattern_type, learning.pattern_type.upper())
        lines.append(f"[{i}] {label} (confidence: {learning.confidence:.1%}, seen {learning.frequency}x)")
        lines.append(f"    {learning.pattern}")
        if learning.evidence:
            evidence_str = ", ".join(learning.evidence[:5])
            if len(learning.evidence) > 5:
                evidence_str += f" (+{len(learning.evidence) - 5} more)"
            lines.append(f"    Evidence: {evidence_str}")
        lines.append(f"    Action: {learning.suggested_action}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="learning_extractor",
        description="Extract actionable patterns from converge run history",
    )
    sub = parser.add_subparsers(dest="command")

    extract_p = sub.add_parser("extract", help="Show all learnings")
    extract_p.add_argument("--min-confidence", type=float, default=0.5,
                           help="Minimum confidence threshold (default: 0.5)")
    extract_p.add_argument("--type", dest="pattern_type",
                           choices=["strategy", "stall", "gene_combo", "gene_anti", "depth_limit", "time"],
                           help="Filter by pattern type")
    extract_p.add_argument("--json", action="store_true", help="Output as JSON")
    extract_p.add_argument("--metrics-path", type=str, default=None,
                           help="Path to converge-metrics.jsonl")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    metrics = Path(args.metrics_path) if args.metrics_path else METRICS_PATH
    report = extract_learnings(metrics, min_confidence=args.min_confidence)

    # Filter by type if requested
    if args.pattern_type:
        report.learnings = [l for l in report.learnings if l.pattern_type == args.pattern_type]
        report.patterns_found = len(report.learnings)

    if args.json:
        output = {
            "total_runs_analyzed": report.total_runs_analyzed,
            "patterns_found": report.patterns_found,
            "learnings": [asdict(l) for l in report.learnings],
        }
        print(json.dumps(output, indent=2))
    else:
        print(format_report(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
