#!/usr/bin/env python3
"""Converge run comparison tool.

Compare two converge runs side by side, showing dimension score deltas,
velocity trajectory changes, gene diffs, and overall improvement assessment.

Usage:
    python3 lib/converge_compare.py                     # compare last 2 runs
    python3 lib/converge_compare.py <run_a> <run_b>     # compare specific runs
    python3 lib/converge_compare.py --json               # last 2 runs as JSON
    python3 lib/converge_compare.py <run_a> <run_b> --json
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

METRICS_PATH = Path(__file__).resolve().parent.parent / "learning" / "converge-metrics.jsonl"


@dataclass
class ComparisonResult:
    """Result of comparing two converge runs."""

    dimension_deltas: dict[str, float]
    velocity_delta: float
    depth_delta: int
    time_delta: float
    gene_overlap: set[str]
    gene_added: set[str]
    gene_removed: set[str]
    improved: bool

    # Keep references to originals for formatting
    run_a: dict = field(default_factory=dict, repr=False)
    run_b: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict (sets become sorted lists)."""
        d = asdict(self)
        d["gene_overlap"] = sorted(d["gene_overlap"])
        d["gene_added"] = sorted(d["gene_added"])
        d["gene_removed"] = sorted(d["gene_removed"])
        # Strip full run data from JSON output — keep only IDs
        d["run_a_id"] = d.pop("run_a", {}).get("run_id", "")
        d["run_b_id"] = d.pop("run_b", {}).get("run_id", "")
        return d


def _load_end_events(path: Path | None = None) -> list[dict]:
    """Load all converge_end events from the metrics JSONL file."""
    p = path or METRICS_PATH
    if not p.exists():
        return []
    events: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event") == "converge_end":
            events.append(record)
    return events


def load_run(run_id: str, *, path: Path | None = None) -> dict | None:
    """Find a converge_end event by run_id. Returns None if not found."""
    for event in _load_end_events(path):
        if event.get("run_id") == run_id:
            return event
    return None


def load_latest(n: int = 2, *, path: Path | None = None) -> list[dict]:
    """Load the last N converge_end events (ordered by file position)."""
    events = _load_end_events(path)
    return events[-n:] if len(events) >= n else events


def _final_velocity(run: dict) -> float:
    """Extract the final velocity score from a run.

    Uses the last element of velocity_trajectory if present,
    otherwise falls back to final_score.
    """
    traj = run.get("velocity_trajectory") or []
    if traj:
        return float(traj[-1])
    return float(run.get("final_score", 0))


def compare_runs(run_a: dict, run_b: dict) -> ComparisonResult:
    """Compare two converge runs, treating A as baseline and B as latest."""
    # Dimension deltas
    dims_a: dict[str, float] = run_a.get("dimension_scores") or {}
    dims_b: dict[str, float] = run_b.get("dimension_scores") or {}
    all_dims = sorted(set(dims_a) | set(dims_b))
    dimension_deltas = {
        dim: float(dims_b.get(dim, 0)) - float(dims_a.get(dim, 0))
        for dim in all_dims
    }

    # Velocity delta (final trajectory point)
    vel_a = _final_velocity(run_a)
    vel_b = _final_velocity(run_b)
    velocity_delta = vel_b - vel_a

    # Depth and time
    depth_delta = int(run_b.get("depth_reached", 0)) - int(run_a.get("depth_reached", 0))
    time_delta = float(run_b.get("elapsed_minutes", 0)) - float(run_a.get("elapsed_minutes", 0))

    # Gene sets
    genes_a = set(run_a.get("invoked_genes") or [])
    genes_b = set(run_b.get("invoked_genes") or [])
    gene_overlap = genes_a & genes_b
    gene_added = genes_b - genes_a
    gene_removed = genes_a - genes_b

    # Improvement assessment: score went up OR satisfied flipped to True
    score_improved = float(run_b.get("final_score", 0)) > float(run_a.get("final_score", 0))
    satisfaction_gained = (not run_a.get("satisfied")) and run_b.get("satisfied", False)
    improved = score_improved or satisfaction_gained

    return ComparisonResult(
        dimension_deltas=dimension_deltas,
        velocity_delta=velocity_delta,
        depth_delta=depth_delta,
        time_delta=time_delta,
        gene_overlap=gene_overlap,
        gene_added=gene_added,
        gene_removed=gene_removed,
        improved=improved,
        run_a=run_a,
        run_b=run_b,
    )


def _arrow(delta: float) -> str:
    """Return a directional indicator for a delta value."""
    if delta > 0:
        return f"+{delta:.1f} ^"
    elif delta < 0:
        return f"{delta:.1f} v"
    return "0.0 ="


def _score_bar(score: float, width: int = 20) -> str:
    """Render a simple text bar for a score (0-100)."""
    filled = int(score / 100 * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def format_comparison(result: ComparisonResult) -> str:
    """Format a ComparisonResult as human-readable text."""
    lines: list[str] = []

    id_a = result.run_a.get("run_id", "?")[:8]
    id_b = result.run_b.get("run_id", "?")[:8]
    task_a = result.run_a.get("task", "")
    task_b = result.run_b.get("task", "")

    lines.append("=" * 60)
    lines.append("CONVERGE RUN COMPARISON")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"  Run A: {id_a}  {task_a}")
    lines.append(f"  Run B: {id_b}  {task_b}")
    lines.append("")

    # Overall scores
    score_a = float(result.run_a.get("final_score", 0))
    score_b = float(result.run_b.get("final_score", 0))
    lines.append("--- Scores ---")
    lines.append(f"  A: {score_a:5.1f}  {_score_bar(score_a)}")
    lines.append(f"  B: {score_b:5.1f}  {_score_bar(score_b)}")
    lines.append(f"  Delta: {_arrow(score_b - score_a)}")
    lines.append("")

    # Dimension scores side by side
    if result.dimension_deltas:
        dims_a = result.run_a.get("dimension_scores") or {}
        dims_b = result.run_b.get("dimension_scores") or {}
        lines.append("--- Dimensions ---")
        lines.append(f"  {'Dimension':<16} {'A':>6}  {'B':>6}  {'Delta':>10}")
        lines.append(f"  {'-' * 42}")
        for dim in sorted(result.dimension_deltas):
            va = float(dims_a.get(dim, 0))
            vb = float(dims_b.get(dim, 0))
            delta = result.dimension_deltas[dim]
            lines.append(f"  {dim:<16} {va:6.1f}  {vb:6.1f}  {_arrow(delta):>10}")
        lines.append("")

    # Velocity trajectory
    traj_a = result.run_a.get("velocity_trajectory") or []
    traj_b = result.run_b.get("velocity_trajectory") or []
    lines.append("--- Velocity ---")
    lines.append(f"  A trajectory: {traj_a if traj_a else '(none)'}")
    lines.append(f"  B trajectory: {traj_b if traj_b else '(none)'}")
    lines.append(f"  Final velocity delta: {_arrow(result.velocity_delta)}")
    lines.append("")

    # Depth and time
    lines.append("--- Depth / Time ---")
    depth_a = result.run_a.get("depth_reached", 0)
    depth_b = result.run_b.get("depth_reached", 0)
    time_a = result.run_a.get("elapsed_minutes", 0)
    time_b = result.run_b.get("elapsed_minutes", 0)
    lines.append(f"  Depth:   A={depth_a}  B={depth_b}  delta={result.depth_delta:+d}")
    lines.append(f"  Time:    A={time_a:.0f}m  B={time_b:.0f}m  delta={result.time_delta:+.0f}m")
    lines.append("")

    # Gene diff
    lines.append("--- Gene Diff ---")
    if result.gene_overlap:
        lines.append(f"  Shared:  {', '.join(sorted(result.gene_overlap))}")
    if result.gene_added:
        lines.append(f"  Added:   {', '.join(sorted(result.gene_added))}")
    if result.gene_removed:
        lines.append(f"  Removed: {', '.join(sorted(result.gene_removed))}")
    if not (result.gene_overlap or result.gene_added or result.gene_removed):
        lines.append("  (no gene data)")
    lines.append("")

    # Verdict
    lines.append("--- Verdict ---")
    sat_a = result.run_a.get("satisfied", False)
    sat_b = result.run_b.get("satisfied", False)
    if result.improved:
        lines.append("  IMPROVED: Run B outperforms Run A")
    elif score_b == score_a and sat_a == sat_b:
        lines.append("  STABLE: Runs are equivalent")
    else:
        lines.append("  REGRESSED: Run B underperforms Run A")
    lines.append("=" * 60)

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = argv if argv is not None else sys.argv[1:]
    use_json = "--json" in args
    run_ids = [a for a in args if not a.startswith("-")]

    if len(run_ids) == 2:
        run_a = load_run(run_ids[0])
        run_b = load_run(run_ids[1])
        if run_a is None:
            print(f"Error: run {run_ids[0]} not found", file=sys.stderr)
            return 1
        if run_b is None:
            print(f"Error: run {run_ids[1]} not found", file=sys.stderr)
            return 1
    elif len(run_ids) == 0:
        latest = load_latest(2)
        if len(latest) < 2:
            print("Error: need at least 2 converge runs to compare", file=sys.stderr)
            return 1
        run_a, run_b = latest[0], latest[1]
    else:
        print("Usage: converge_compare.py [<run_a> <run_b>] [--json]", file=sys.stderr)
        return 2

    result = compare_runs(run_a, run_b)

    if use_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_comparison(result))

    return 0


if __name__ == "__main__":
    sys.exit(main())
