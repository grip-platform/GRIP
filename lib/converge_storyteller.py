#!/usr/bin/env python3
"""Convergence Storyteller -- natural language narratives from convergence run data.

Reads JSONL metrics and genome data to produce human-readable stories
describing convergence runs, trends, anomalies, and gene contributions.
"""

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Narrative:
    """A single narrative block."""
    run_id: str
    text: str
    highlights: list[str] = field(default_factory=list)
    sentiment: str = "neutral"  # positive, negative, neutral


@dataclass
class TrendAnalysis:
    """Multi-run trend analysis."""
    trend: str  # improving, declining, plateau, oscillating
    confidence: float  # 0-1
    description: str
    window_size: int = 0
    dimension_trends: dict[str, str] = field(default_factory=dict)


@dataclass
class GeneContribution:
    """Gene contribution analysis."""
    gene: str
    appearances: int
    success_rate: float
    avg_score: float
    description: str


# ---------------------------------------------------------------------------
# Template pools for variation
# ---------------------------------------------------------------------------

_OPENING_TEMPLATES = [
    "Convergence run '{task}' reached depth {depth} of {max_depth}.",
    "The run on '{task}' converged at depth {depth} (max {max_depth}).",
    "Run '{task}' completed, reaching depth {depth} out of {max_depth}.",
    "'{task}' was processed through {depth} convergence depths (limit: {max_depth}).",
]

_SUCCESS_TEMPLATES = [
    "The convergence criterion was satisfied.",
    "All dimensions met the satisfaction threshold.",
    "The run achieved convergence successfully.",
    "Criterion satisfied -- the task converged.",
]

_FAILURE_TEMPLATES = [
    "The run did not satisfy the convergence criterion.",
    "Convergence was not achieved within the depth limit.",
    "The task failed to converge.",
    "Criterion was not met -- the run fell short.",
]

_VELOCITY_IMPROVING = [
    "Velocity showed a steady upward trajectory ({trajectory}).",
    "Scores accelerated through the run ({trajectory}).",
    "The velocity curve climbed consistently ({trajectory}).",
]

_VELOCITY_DECLINING = [
    "Velocity declined through the run ({trajectory}).",
    "Scores decelerated as the run progressed ({trajectory}).",
    "The velocity curve trended downward ({trajectory}).",
]

_VELOCITY_STABLE = [
    "Velocity remained stable throughout ({trajectory}).",
    "Scores held steady across iterations ({trajectory}).",
]

_TREND_IMPROVING = [
    "Over {n} runs, convergence quality is improving.",
    "The last {n} runs show a clear upward trend.",
    "Quality metrics are trending upward across {n} runs.",
]

_TREND_DECLINING = [
    "Over {n} runs, convergence quality is declining.",
    "The last {n} runs reveal a downward trajectory.",
    "Quality metrics are dropping across {n} runs.",
]

_TREND_PLATEAU = [
    "Over {n} runs, convergence quality has plateaued.",
    "The last {n} runs show stable, flat metrics.",
    "Quality metrics are holding steady across {n} runs.",
]

_TREND_OSCILLATING = [
    "Over {n} runs, convergence quality is oscillating.",
    "The last {n} runs show alternating ups and downs.",
    "Quality metrics are fluctuating across {n} runs.",
]

_COMPARISON_TEMPLATES = [
    "Comparing '{t1}' and '{t2}':",
    "Run '{t1}' vs '{t2}':",
    "Side-by-side: '{t1}' and '{t2}'.",
]

_ANOMALY_TEMPLATES = [
    "Anomaly detected: {description}.",
    "Unusual pattern: {description}.",
    "Notable outlier: {description}.",
]

_MVP_TEMPLATES = [
    "Gene '{gene}' was the MVP, appearing in {n} successful runs with a {rate:.0%} success rate.",
    "The standout gene was '{gene}' -- {n} appearances in successful runs, {rate:.0%} success rate.",
    "'{gene}' dominated successful convergences: {n} appearances, {rate:.0%} success rate.",
]


def _pick(templates: list[str], **kwargs) -> str:
    """Pick a random template and format it."""
    return random.choice(templates).format(**kwargs)


# ---------------------------------------------------------------------------
# Core analysis helpers
# ---------------------------------------------------------------------------

def _avg_final_score(run: dict) -> float:
    """Average of all final dimension scores."""
    scores = run.get("final_dimension_scores", {})
    if not scores:
        return 0.0
    return sum(scores.values()) / len(scores)


def _detect_velocity_trend(trajectory: list[float]) -> str:
    """Classify a velocity trajectory."""
    if len(trajectory) < 2:
        return "stable"
    diffs = [trajectory[i + 1] - trajectory[i] for i in range(len(trajectory) - 1)]
    positive = sum(1 for d in diffs if d > 0)
    negative = sum(1 for d in diffs if d < 0)
    if positive == len(diffs):
        return "improving"
    if negative == len(diffs):
        return "declining"
    if positive > negative:
        return "improving"
    if negative > positive:
        return "declining"
    return "stable"


def _detect_trend(scores: list[float]) -> str:
    """Detect trend across a sequence of scores.

    Returns: improving, declining, plateau, oscillating
    """
    if len(scores) < 2:
        return "plateau"

    diffs = [scores[i + 1] - scores[i] for i in range(len(scores) - 1)]
    positive = sum(1 for d in diffs if d > 2)
    negative = sum(1 for d in diffs if d < -2)
    neutral = len(diffs) - positive - negative

    # Oscillating: frequent direction changes
    direction_changes = 0
    for i in range(len(diffs) - 1):
        if (diffs[i] > 0 and diffs[i + 1] < 0) or (diffs[i] < 0 and diffs[i + 1] > 0):
            direction_changes += 1

    if len(diffs) >= 3 and direction_changes >= len(diffs) * 0.5:
        return "oscillating"

    if neutral >= len(diffs) * 0.7:
        return "plateau"
    if positive > negative:
        return "improving"
    if negative > positive:
        return "declining"
    return "plateau"


def _score_delta_description(dim: str, before: float, after: float) -> str:
    """Describe a single dimension score change."""
    delta = after - before
    if abs(delta) < 2:
        return f"{dim} held steady at {after:.0f}"
    direction = "rose" if delta > 0 else "dropped"
    return f"{dim} {direction} from {before:.0f} to {after:.0f} ({delta:+.0f})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_narrative(run_data: dict) -> Narrative:
    """Produce a paragraph describing a single convergence run."""
    task = run_data.get("task", "unknown task")
    run_id = run_data.get("run_id", "unknown")
    max_depth = run_data.get("max_depth", 0)
    depth_reached = run_data.get("depth_reached", 0)
    satisfied = run_data.get("satisfied", False)
    elapsed = run_data.get("elapsed_minutes", 0)
    trajectory = run_data.get("velocity_trajectory", [])
    final_scores = run_data.get("final_dimension_scores", {})
    expressed = run_data.get("expressed_genes", [])
    weaknesses_all = []
    for d in run_data.get("depths", []):
        weaknesses_all.extend(d.get("weaknesses", []))

    parts = []

    # Opening
    parts.append(_pick(_OPENING_TEMPLATES, task=task, depth=depth_reached, max_depth=max_depth))

    # Outcome
    if satisfied:
        parts.append(_pick(_SUCCESS_TEMPLATES))
    else:
        parts.append(_pick(_FAILURE_TEMPLATES))

    # Final scores
    if final_scores:
        score_parts = [f"{dim}: {val}" for dim, val in sorted(final_scores.items())]
        avg = _avg_final_score(run_data)
        parts.append(f"Final scores -- {', '.join(score_parts)} (avg {avg:.1f}).")

    # Velocity
    if trajectory:
        vel_trend = _detect_velocity_trend(trajectory)
        traj_str = " -> ".join(str(v) for v in trajectory)
        if vel_trend == "improving":
            parts.append(_pick(_VELOCITY_IMPROVING, trajectory=traj_str))
        elif vel_trend == "declining":
            parts.append(_pick(_VELOCITY_DECLINING, trajectory=traj_str))
        else:
            parts.append(_pick(_VELOCITY_STABLE, trajectory=traj_str))

    # Elapsed
    if elapsed:
        parts.append(f"Completed in {elapsed:.1f} minutes.")

    # Genes
    if expressed:
        parts.append(f"Active genes: {', '.join(expressed)}.")

    # Weaknesses
    if weaknesses_all:
        unique_w = list(dict.fromkeys(weaknesses_all))  # dedupe preserving order
        parts.append(f"Weaknesses identified: {'; '.join(unique_w)}.")

    highlights = []
    if satisfied:
        highlights.append("converged")
    if final_scores:
        best_dim = max(final_scores, key=final_scores.get)
        highlights.append(f"best dimension: {best_dim}")
    if trajectory and _detect_velocity_trend(trajectory) == "improving":
        highlights.append("accelerating velocity")

    sentiment = "positive" if satisfied else "negative"
    avg = _avg_final_score(run_data)
    if satisfied and avg >= 90:
        sentiment = "positive"
    elif satisfied:
        sentiment = "neutral"

    return Narrative(
        run_id=run_id,
        text=" ".join(parts),
        highlights=highlights,
        sentiment=sentiment,
    )


def generate_trend_narrative(runs: list[dict]) -> TrendAnalysis:
    """Describe trends across multiple runs."""
    if not runs:
        return TrendAnalysis(
            trend="plateau",
            confidence=0.0,
            description="No runs available for trend analysis.",
            window_size=0,
        )

    if len(runs) == 1:
        return TrendAnalysis(
            trend="plateau",
            confidence=0.5,
            description="Only one run available -- insufficient data for trend detection.",
            window_size=1,
        )

    avg_scores = [_avg_final_score(r) for r in runs]
    trend = _detect_trend(avg_scores)
    n = len(runs)

    # Confidence based on consistency
    if n >= 5:
        confidence = 0.9
    elif n >= 3:
        confidence = 0.7
    else:
        confidence = 0.5

    trend_templates = {
        "improving": _TREND_IMPROVING,
        "declining": _TREND_DECLINING,
        "plateau": _TREND_PLATEAU,
        "oscillating": _TREND_OSCILLATING,
    }
    description = _pick(trend_templates.get(trend, _TREND_PLATEAU), n=n)

    # Per-dimension trends
    all_dims = set()
    for r in runs:
        all_dims.update(r.get("final_dimension_scores", {}).keys())

    dimension_trends = {}
    for dim in sorted(all_dims):
        dim_scores = [
            r.get("final_dimension_scores", {}).get(dim, 0)
            for r in runs
            if dim in r.get("final_dimension_scores", {})
        ]
        if len(dim_scores) >= 2:
            dimension_trends[dim] = _detect_trend(dim_scores)

    # Enrich description with dimension detail
    if dimension_trends:
        dim_parts = [f"{dim} ({t})" for dim, t in dimension_trends.items()]
        description += f" Per-dimension: {', '.join(dim_parts)}."

    # Success rate
    success_count = sum(1 for r in runs if r.get("satisfied", False))
    success_rate = success_count / n
    description += f" Success rate: {success_rate:.0%} ({success_count}/{n})."

    return TrendAnalysis(
        trend=trend,
        confidence=confidence,
        description=description,
        window_size=n,
        dimension_trends=dimension_trends,
    )


def identify_mvp_gene(runs: list[dict]) -> Optional[GeneContribution]:
    """Find the gene that appeared most in successful runs."""
    if not runs:
        return None

    gene_stats: dict[str, dict] = {}  # gene -> {total, success, scores}

    for run in runs:
        satisfied = run.get("satisfied", False)
        avg = _avg_final_score(run)
        for gene in run.get("expressed_genes", []):
            if gene not in gene_stats:
                gene_stats[gene] = {"total": 0, "success": 0, "scores": []}
            gene_stats[gene]["total"] += 1
            if satisfied:
                gene_stats[gene]["success"] += 1
            gene_stats[gene]["scores"].append(avg)

    if not gene_stats:
        return None

    # MVP = most appearances in successful runs, tiebreak by success rate
    mvp_gene = max(
        gene_stats,
        key=lambda g: (gene_stats[g]["success"], gene_stats[g]["success"] / max(gene_stats[g]["total"], 1)),
    )
    stats = gene_stats[mvp_gene]
    rate = stats["success"] / max(stats["total"], 1)
    avg_score = sum(stats["scores"]) / len(stats["scores"]) if stats["scores"] else 0

    description = _pick(
        _MVP_TEMPLATES,
        gene=mvp_gene,
        n=stats["success"],
        rate=rate,
    )

    return GeneContribution(
        gene=mvp_gene,
        appearances=stats["total"],
        success_rate=rate,
        avg_score=avg_score,
        description=description,
    )


def narrate_anomalies(runs: list[dict]) -> list[str]:
    """Describe anomalous patterns in natural language."""
    if len(runs) < 3:
        return []

    anomalies = []
    avg_scores = [_avg_final_score(r) for r in runs]
    overall_avg = sum(avg_scores) / len(avg_scores)
    overall_std = (sum((s - overall_avg) ** 2 for s in avg_scores) / len(avg_scores)) ** 0.5

    for i, run in enumerate(runs):
        score = avg_scores[i]
        task = run.get("task", "unknown")
        run_id = run.get("run_id", "?")

        # Score anomaly: > 2 std dev from mean
        if overall_std > 0 and abs(score - overall_avg) > 2 * overall_std:
            direction = "above" if score > overall_avg else "below"
            anomalies.append(
                _pick(
                    _ANOMALY_TEMPLATES,
                    description=f"run '{task}' ({run_id}) scored {score:.0f}, "
                    f"which is {abs(score - overall_avg):.0f} points {direction} "
                    f"the average of {overall_avg:.0f}",
                )
            )

        # Depth anomaly: satisfied at depth 0
        if run.get("satisfied") and run.get("depth_reached", 99) == 0:
            anomalies.append(
                _pick(
                    _ANOMALY_TEMPLATES,
                    description=f"run '{task}' ({run_id}) converged at depth 0 "
                    f"-- immediate satisfaction without iteration",
                )
            )

        # Speed anomaly: very fast or very slow
        elapsed = run.get("elapsed_minutes", 0)
        elapsed_vals = [r.get("elapsed_minutes", 0) for r in runs if r.get("elapsed_minutes", 0) > 0]
        if elapsed > 0 and elapsed_vals:
            avg_elapsed = sum(elapsed_vals) / len(elapsed_vals)
            if elapsed > avg_elapsed * 3:
                anomalies.append(
                    _pick(
                        _ANOMALY_TEMPLATES,
                        description=f"run '{task}' ({run_id}) took {elapsed:.1f} min, "
                        f"3x longer than the average of {avg_elapsed:.1f} min",
                    )
                )
            elif elapsed < avg_elapsed * 0.2 and avg_elapsed > 1:
                anomalies.append(
                    _pick(
                        _ANOMALY_TEMPLATES,
                        description=f"run '{task}' ({run_id}) completed in {elapsed:.1f} min, "
                        f"far faster than the average of {avg_elapsed:.1f} min",
                    )
                )

        # Velocity reversal: started high, ended low
        traj = run.get("velocity_trajectory", [])
        if len(traj) >= 3 and traj[0] > traj[-1] and traj[0] > 70:
            anomalies.append(
                _pick(
                    _ANOMALY_TEMPLATES,
                    description=f"run '{task}' ({run_id}) showed velocity reversal: "
                    f"started at {traj[0]}, ended at {traj[-1]}",
                )
            )

    return anomalies


def compare_runs(run1: dict, run2: dict) -> str:
    """Narrative comparison of two runs."""
    t1 = run1.get("task", "run 1")
    t2 = run2.get("task", "run 2")

    parts = [_pick(_COMPARISON_TEMPLATES, t1=t1, t2=t2)]

    # Outcome
    s1 = run1.get("satisfied", False)
    s2 = run2.get("satisfied", False)
    if s1 and s2:
        parts.append("Both runs converged successfully.")
    elif s1 and not s2:
        parts.append(f"'{t1}' converged but '{t2}' did not.")
    elif not s1 and s2:
        parts.append(f"'{t2}' converged but '{t1}' did not.")
    else:
        parts.append("Neither run achieved convergence.")

    # Depth
    d1 = run1.get("depth_reached", 0)
    d2 = run2.get("depth_reached", 0)
    if d1 != d2:
        deeper = t1 if d1 > d2 else t2
        parts.append(f"'{deeper}' went deeper ({max(d1, d2)} vs {min(d1, d2)}).")
    else:
        parts.append(f"Both reached depth {d1}.")

    # Scores
    scores1 = run1.get("final_dimension_scores", {})
    scores2 = run2.get("final_dimension_scores", {})
    all_dims = sorted(set(scores1.keys()) | set(scores2.keys()))
    if all_dims:
        score_parts = []
        for dim in all_dims:
            v1 = scores1.get(dim, 0)
            v2 = scores2.get(dim, 0)
            if v1 > v2:
                score_parts.append(f"{dim}: '{t1}' leads ({v1} vs {v2})")
            elif v2 > v1:
                score_parts.append(f"{dim}: '{t2}' leads ({v2} vs {v1})")
            else:
                score_parts.append(f"{dim}: tied at {v1}")
        parts.append("Dimension breakdown -- " + "; ".join(score_parts) + ".")

    # Speed
    e1 = run1.get("elapsed_minutes", 0)
    e2 = run2.get("elapsed_minutes", 0)
    if e1 and e2:
        faster = t1 if e1 < e2 else t2
        parts.append(f"'{faster}' was faster ({min(e1, e2):.1f} min vs {max(e1, e2):.1f} min).")

    # Overall verdict
    avg1 = _avg_final_score(run1)
    avg2 = _avg_final_score(run2)
    if avg1 > avg2 + 5:
        parts.append(f"Overall, '{t1}' outperformed '{t2}' (avg {avg1:.0f} vs {avg2:.0f}).")
    elif avg2 > avg1 + 5:
        parts.append(f"Overall, '{t2}' outperformed '{t1}' (avg {avg2:.0f} vs {avg1:.0f}).")
    else:
        parts.append(f"Overall scores were comparable (avg {avg1:.0f} vs {avg2:.0f}).")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_runs(path: Path) -> list[dict]:
    """Load convergence runs from a JSONL file."""
    if not path.exists():
        return []
    runs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return runs


def _recent_runs(runs: list[dict], days: int = 7) -> list[dict]:
    """Filter runs to the last N days."""
    cutoff = datetime.now() - timedelta(days=days)
    result = []
    for r in runs:
        ts = r.get("timestamp", "")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                if dt >= cutoff:
                    result.append(r)
            except (ValueError, TypeError):
                pass
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_metrics_path() -> Path:
    """Resolve default metrics JSONL path."""
    claude_dir = Path.home() / ".claude"
    return claude_dir / "data" / "converge-metrics.jsonl"


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="converge-storyteller",
        description="Natural language narratives from convergence run data.",
    )
    parser.add_argument(
        "--file", "-f",
        type=Path,
        default=_default_metrics_path(),
        help="Path to converge-metrics.jsonl",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("latest", help="Narrate the most recent run")
    week_parser = sub.add_parser("week", help="Narrate the last 7 days")
    week_parser.add_argument("--days", type=int, default=7, help="Number of days")
    compare_parser = sub.add_parser("compare", help="Compare two runs")
    compare_parser.add_argument("id1", help="First run ID")
    compare_parser.add_argument("id2", help="Second run ID")
    sub.add_parser("trends", help="Trend analysis")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    runs = load_runs(args.file)

    if args.command == "latest":
        if not runs:
            print("No runs found.")
            return 1
        narrative = generate_narrative(runs[-1])
        print(narrative.text)
        if narrative.highlights:
            print(f"Highlights: {', '.join(narrative.highlights)}")
        return 0

    elif args.command == "week":
        recent = _recent_runs(runs, days=args.days)
        if not recent:
            print(f"No runs in the last {args.days} days.")
            return 1
        trend = generate_trend_narrative(recent)
        print(trend.description)
        mvp = identify_mvp_gene(recent)
        if mvp:
            print(mvp.description)
        anomalies = narrate_anomalies(recent)
        for a in anomalies:
            print(f"  {a}")
        return 0

    elif args.command == "compare":
        id_map = {r.get("run_id"): r for r in runs}
        r1 = id_map.get(args.id1)
        r2 = id_map.get(args.id2)
        if not r1:
            print(f"Run '{args.id1}' not found.")
            return 1
        if not r2:
            print(f"Run '{args.id2}' not found.")
            return 1
        print(compare_runs(r1, r2))
        return 0

    elif args.command == "trends":
        if not runs:
            print("No runs found.")
            return 1
        trend = generate_trend_narrative(runs)
        print(trend.description)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
