#!/usr/bin/env python3
"""
contraction_fitness.py -- Goodhart-resistant fitness measurement using contraction mapping theory.

Instead of measuring absolute scores (which attract to 91 via Goodhart's Law),
this module measures whether the improvement trajectory is genuinely converging
using Banach's fixed-point theorem and contraction mapping analysis.

Key insight: a sequence is "truly converging" when the ratio of successive
differences (the contraction ratio) is consistently < 1. This is structurally
resistant to score inflation because it measures the *shape* of improvement,
not its absolute value.

Usage:
    from lib.contraction_fitness import is_contractive, convergence_quality

    scores = [45, 72, 85, 91, 93]
    contractive, ratio = is_contractive(scores)
    quality = convergence_quality(scores)

CLI:
    python3 lib/contraction_fitness.py check [run_id]
    python3 lib/contraction_fitness.py summary
    python3 lib/contraction_fitness.py trajectory
    # Add --json for machine-readable output
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
CONVERGE_METRICS_PATH = CLAUDE_DIR / "learning" / "converge-metrics.jsonl"


@dataclass
class ConvergenceQuality:
    """Result of convergence quality analysis."""

    is_contractive: bool
    contraction_ratio: float
    lipschitz: float
    trajectory_type: str  # "converging", "oscillating", "diverging", "plateau"
    estimated_fixed_point: float | None
    confidence: float  # 0-1


def is_contractive(scores: list[float]) -> tuple[bool, float]:
    """Determine if a score sequence is contractive.

    A sequence is contractive if |s[i+1] - s[i]| / |s[i] - s[i-1]| < 1
    for the majority of steps. The contraction ratio is the average of
    these ratios (lower = faster convergence).

    Args:
        scores: List of scores in chronological order.

    Returns:
        (is_converging, contraction_ratio) tuple.
        Requires at least 3 data points; returns (False, 0.0) otherwise.
    """
    if len(scores) < 3:
        return (False, 0.0)

    ratios = []
    for i in range(2, len(scores)):
        prev_delta = abs(scores[i - 1] - scores[i - 2])
        curr_delta = abs(scores[i] - scores[i - 1])

        if prev_delta == 0:
            # Previous step had no change; skip this ratio
            continue

        ratios.append(curr_delta / prev_delta)

    if not ratios:
        # All previous deltas were zero (constant sequence)
        return (True, 0.0)

    avg_ratio = sum(ratios) / len(ratios)
    contracting_count = sum(1 for r in ratios if r < 1.0)
    is_converging = contracting_count > len(ratios) / 2

    return (is_converging, round(avg_ratio, 4))


def lipschitz_constant(scores: list[float]) -> float:
    """Estimate the Lipschitz constant of the improvement mapping.

    Models the improvement process as a mapping f where f(s_n) = s_{n+1}.
    The Lipschitz constant L is the maximum ratio of output change to input change:
        L = max(|s[i+1] - s[i]| / |s[i] - s[i-1]|)

    L < 1 means the map is a contraction (guaranteed convergence by Banach).
    L >= 1 means potential divergence or oscillation.

    Args:
        scores: List of scores in chronological order.

    Returns:
        The Lipschitz constant. Returns 0.0 if fewer than 3 data points.
    """
    if len(scores) < 3:
        return 0.0

    max_ratio = 0.0
    for i in range(2, len(scores)):
        prev_delta = abs(scores[i - 1] - scores[i - 2])
        curr_delta = abs(scores[i] - scores[i - 1])

        if prev_delta == 0:
            if curr_delta > 0:
                # Infinite ratio -- divergence from plateau
                return float("inf")
            continue

        ratio = curr_delta / prev_delta
        max_ratio = max(max_ratio, ratio)

    return round(max_ratio, 4)


def detect_trajectory_type(scores: list[float], epsilon: float = 0.5) -> str:
    """Classify the trajectory shape of a score sequence.

    Args:
        scores: List of scores in chronological order.
        epsilon: Threshold for considering a delta "near zero" (plateau detection).

    Returns:
        One of: "converging", "oscillating", "diverging", "plateau"
    """
    if len(scores) < 2:
        return "plateau"

    deltas = [scores[i] - scores[i - 1] for i in range(1, len(scores))]

    # Plateau: all deltas near zero
    if all(abs(d) <= epsilon for d in deltas):
        return "plateau"

    # Oscillating: sign changes in deltas (alternating above/below)
    sign_changes = 0
    for i in range(1, len(deltas)):
        if deltas[i] * deltas[i - 1] < 0:
            sign_changes += 1

    if len(deltas) >= 2 and sign_changes >= len(deltas) / 2:
        return "oscillating"

    # Diverging: absolute deltas increasing
    abs_deltas = [abs(d) for d in deltas]
    if len(abs_deltas) >= 2:
        increasing_count = sum(
            1 for i in range(1, len(abs_deltas)) if abs_deltas[i] > abs_deltas[i - 1]
        )
        if increasing_count > len(abs_deltas) / 2:
            return "diverging"

    return "converging"


def estimated_fixed_point(scores: list[float]) -> float | None:
    """Extrapolate the convergence target using Aitken's delta-squared method.

    Aitken's method accelerates convergence estimation:
        s_hat = s_n - (s_{n+1} - s_n)^2 / (s_{n+2} - 2*s_{n+1} + s_n)

    Returns None if not enough data, not converging, or denominator is zero.

    Args:
        scores: List of scores in chronological order.

    Returns:
        The estimated fixed point, or None.
    """
    if len(scores) < 3:
        return None

    # Use the last three points for the most recent estimate
    s0 = scores[-3]
    s1 = scores[-2]
    s2 = scores[-1]

    denominator = s2 - 2 * s1 + s0
    if abs(denominator) < 1e-10:
        return None

    numerator = (s1 - s0) ** 2
    estimate = s0 - numerator / denominator

    # Sanity check: estimate should be finite and reasonable
    if not math.isfinite(estimate):
        return None

    return round(estimate, 2)


def convergence_quality(scores: list[float]) -> ConvergenceQuality:
    """Comprehensive convergence quality analysis.

    Combines all metrics into a single ConvergenceQuality result.

    Args:
        scores: List of scores in chronological order.

    Returns:
        ConvergenceQuality dataclass with all analysis fields.
    """
    contractive, ratio = is_contractive(scores)
    lip = lipschitz_constant(scores)
    traj_type = detect_trajectory_type(scores)
    fixed_pt = estimated_fixed_point(scores)

    # Confidence: based on data points and consistency
    n = len(scores)
    if n < 2:
        confidence = 0.0
    elif n < 3:
        confidence = 0.2
    elif n < 5:
        confidence = 0.5
    else:
        confidence = min(0.9, 0.5 + 0.1 * (n - 4))

    # Boost confidence if trajectory is consistent
    if traj_type == "converging" and contractive:
        confidence = min(1.0, confidence + 0.1)
    # Reduce confidence for oscillating/diverging
    if traj_type in ("oscillating", "diverging"):
        confidence = max(0.0, confidence - 0.1)

    return ConvergenceQuality(
        is_contractive=contractive,
        contraction_ratio=ratio,
        lipschitz=lip,
        trajectory_type=traj_type,
        estimated_fixed_point=fixed_pt,
        confidence=round(confidence, 2),
    )


# ---------------------------------------------------------------------------
# JSONL loading and run analysis
# ---------------------------------------------------------------------------


def _load_runs(path: Path | None = None) -> dict[str, dict]:
    """Load converge runs from JSONL, grouped by run_id.

    Returns:
        Dict mapping run_id to run data with keys:
            task, task_type, depths (list of {depth, score, ...}), final_score, ...
    """
    jsonl_path = path or CONVERGE_METRICS_PATH
    if not jsonl_path.exists():
        return {}

    events: dict[str, list[dict]] = {}
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            run_id = event.get("run_id", "")
            if run_id:
                events.setdefault(run_id, []).append(event)

    runs: dict[str, dict] = {}
    for run_id, run_events in events.items():
        run: dict = {"run_id": run_id, "depths": []}
        for ev in run_events:
            event_type = ev.get("event", "")
            if event_type == "converge_start":
                run["task"] = ev.get("task", "")
                run["task_type"] = ev.get("task_type", "")
                run["max_depth"] = ev.get("max_depth", 0)
                run["timestamp"] = ev.get("timestamp", "")
            elif event_type == "converge_depth":
                run["depths"].append(ev)
            elif event_type == "converge_end":
                run["final_score"] = ev.get("final_score")
                run["satisfied"] = ev.get("satisfied", False)
                run["depth_reached"] = ev.get("depth_reached", 0)

        # Sort depths by depth number
        run["depths"].sort(key=lambda d: d.get("depth", 0))
        runs[run_id] = run

    return runs


def _extract_trajectory(run: dict) -> list[float]:
    """Extract the score trajectory from a run."""
    return [d.get("score", 0) for d in run.get("depths", []) if "score" in d]


def analyze_run(run_id: str, path: Path | None = None) -> ConvergenceQuality:
    """Load and analyze a specific converge run.

    Args:
        run_id: The run ID to analyze.
        path: Optional path to the JSONL file.

    Returns:
        ConvergenceQuality for the run.

    Raises:
        KeyError: If run_id not found.
    """
    runs = _load_runs(path)
    if run_id not in runs:
        raise KeyError(f"Run '{run_id}' not found in converge metrics")

    trajectory = _extract_trajectory(runs[run_id])
    return convergence_quality(trajectory)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def statistical_proof(path: Path | None = None) -> dict:
    """Generate statistical proof of contraction when N>=100.

    Computes aggregate contraction ratio with 95% confidence interval
    using the normal approximation. If CI upper bound < 1.0, the
    convergence process is statistically proven contractive.

    Returns:
        Dict with n, mean_L, ci_lower, ci_upper, proven fields.
    """
    runs = _load_runs(path)
    ratios: list[float] = []
    for run in runs.values():
        traj = _extract_trajectory(run)
        if len(traj) < 3:
            continue
        _, ratio = is_contractive(traj)
        if ratio > 0:
            ratios.append(ratio)

    n = len(ratios)
    if n == 0:
        return {"n": 0, "mean_L": 0.0, "proven": False, "reason": "no data"}

    mean_l = sum(ratios) / n
    if n < 2:
        return {"n": n, "mean_L": round(mean_l, 4), "proven": False, "reason": "insufficient data"}

    variance = sum((r - mean_l) ** 2 for r in ratios) / (n - 1)
    std_err = math.sqrt(variance / n)
    z = 1.96  # 95% CI
    ci_lower = mean_l - z * std_err
    ci_upper = mean_l + z * std_err

    return {
        "n": n,
        "mean_L": round(mean_l, 4),
        "std": round(math.sqrt(variance), 4),
        "ci_lower": round(ci_lower, 4),
        "ci_upper": round(ci_upper, 4),
        "proven": ci_upper < 1.0,
    }


def _format_quality(q: ConvergenceQuality, run_id: str = "", task: str = "") -> str:
    """Format a ConvergenceQuality for human display."""
    lines = []
    if run_id:
        header = f"Run {run_id}"
        if task:
            header += f": {task}"
        lines.append(header)
        lines.append("-" * len(header))

    status = "CONTRACTIVE" if q.is_contractive else "NOT CONTRACTIVE"
    lines.append(f"  Status:           {status}")
    lines.append(f"  Contraction ratio: {q.contraction_ratio:.4f}")
    lines.append(f"  Lipschitz constant: {q.lipschitz}")
    lines.append(f"  Trajectory:        {q.trajectory_type}")
    if q.estimated_fixed_point is not None:
        lines.append(f"  Est. fixed point:  {q.estimated_fixed_point}")
    lines.append(f"  Confidence:        {q.confidence:.2f}")
    return "\n".join(lines)


def cmd_check(args: argparse.Namespace) -> int:
    """Analyze a specific run."""
    try:
        quality = analyze_run(args.run_id)
    except KeyError as e:
        print(str(e), file=sys.stderr)
        return 1

    runs = _load_runs()
    run = runs.get(args.run_id, {})
    task = run.get("task", "")

    if args.json:
        print(json.dumps(asdict(quality), indent=2))
    else:
        trajectory = _extract_trajectory(run)
        print(_format_quality(quality, args.run_id, task))
        if trajectory:
            print(f"  Trajectory:        {trajectory}")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    """Analyze all runs and show convergence summary."""
    runs = _load_runs()
    if not runs:
        print("No converge runs found.")
        return 0

    results = []
    for run_id, run in runs.items():
        trajectory = _extract_trajectory(run)
        if not trajectory:
            continue
        quality = convergence_quality(trajectory)
        results.append(
            {
                "run_id": run_id,
                "task": run.get("task", ""),
                "trajectory": trajectory,
                "quality": quality,
            }
        )

    if args.json:
        output = [
            {"run_id": r["run_id"], "task": r["task"], **asdict(r["quality"])}
            for r in results
        ]
        print(json.dumps(output, indent=2))
        return 0

    converging = sum(1 for r in results if r["quality"].is_contractive)
    total = len(results)

    print(f"Convergence Summary: {converging}/{total} runs are genuinely contractive\n")

    for r in results:
        q = r["quality"]
        status = "OK" if q.is_contractive else "!!"
        traj = r["trajectory"]
        traj_str = " -> ".join(str(int(s)) for s in traj)
        print(
            f"  [{status}] {r['run_id'][:8]}  "
            f"L={q.contraction_ratio:.3f}  "
            f"{q.trajectory_type:<12}  "
            f"{traj_str}  "
            f"({r['task'][:40]})"
        )

    return 0


def cmd_trajectory(args: argparse.Namespace) -> int:
    """Show all run trajectories with contraction ratios."""
    runs = _load_runs()
    if not runs:
        print("No converge runs found.")
        return 0

    results = []
    for run_id, run in runs.items():
        trajectory = _extract_trajectory(run)
        if len(trajectory) < 2:
            continue

        # Compute per-step contraction ratios
        step_ratios = []
        for i in range(2, len(trajectory)):
            prev_delta = abs(trajectory[i - 1] - trajectory[i - 2])
            curr_delta = abs(trajectory[i] - trajectory[i - 1])
            if prev_delta == 0:
                step_ratios.append(None)
            else:
                step_ratios.append(round(curr_delta / prev_delta, 3))

        quality = convergence_quality(trajectory)
        results.append(
            {
                "run_id": run_id,
                "task": run.get("task", ""),
                "trajectory": trajectory,
                "step_ratios": step_ratios,
                "quality": quality,
            }
        )

    if args.json:
        output = [
            {
                "run_id": r["run_id"],
                "task": r["task"],
                "trajectory": r["trajectory"],
                "step_ratios": r["step_ratios"],
                **asdict(r["quality"]),
            }
            for r in results
        ]
        print(json.dumps(output, indent=2))
        return 0

    for r in results:
        q = r["quality"]
        print(f"\n{r['run_id'][:8]}: {r['task'][:50]}")
        print(f"  Scores: {r['trajectory']}")
        print(f"  Ratios: {r['step_ratios']}")
        print(
            f"  Type: {q.trajectory_type} | "
            f"L={q.contraction_ratio:.3f} | "
            f"Lip={q.lipschitz} | "
            f"Conf={q.confidence:.2f}"
        )
        if q.estimated_fixed_point is not None:
            print(f"  Est. fixed point: {q.estimated_fixed_point}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Contraction-based fitness measurement for converge runs"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output in JSON format"
    )

    subparsers = parser.add_subparsers(dest="command")

    check_parser = subparsers.add_parser("check", help="Analyze a specific run")
    check_parser.add_argument("run_id", help="The run ID to analyze")

    subparsers.add_parser("summary", help="Analyze all runs")
    subparsers.add_parser("trajectory", help="Show all trajectories with ratios")
    subparsers.add_parser("proof", help="Statistical proof of contraction (N>=100)")

    args = parser.parse_args()

    if args.command == "check":
        return cmd_check(args)
    elif args.command == "summary":
        return cmd_summary(args)
    elif args.command == "trajectory":
        return cmd_trajectory(args)
    elif args.command == "proof":
        result = statistical_proof()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"N={result['n']}  mean_L={result.get('mean_L', 0):.4f}  "
                  f"CI=[{result.get('ci_lower', 0):.4f}, {result.get('ci_upper', 0):.4f}]  "
                  f"proven={'YES' if result.get('proven') else 'NO'}")
        return 0
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
