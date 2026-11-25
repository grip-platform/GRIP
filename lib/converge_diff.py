#!/usr/bin/env python3
"""Convergence diff viewer -- side-by-side depth comparison.

Shows what changed between convergence depths: dimension score deltas,
weaknesses addressed/introduced, strategy changes. ASCII table output.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class DepthSnapshot:
    """State at a single convergence depth."""

    depth: int
    dimension_scores: dict[str, float] = field(default_factory=dict)
    weaknesses: list[str] = field(default_factory=list)
    strategy: str = ""

    @property
    def composite(self) -> float:
        """Average of all dimension scores, or 0.0 if none."""
        if not self.dimension_scores:
            return 0.0
        return sum(self.dimension_scores.values()) / len(self.dimension_scores)


@dataclass
class DepthDiff:
    """Diff between two depth snapshots."""

    depth_before: int
    depth_after: int
    dimension_deltas: dict[str, float] = field(default_factory=dict)
    composite_before: float = 0.0
    composite_after: float = 0.0
    composite_delta: float = 0.0
    weaknesses_resolved: list[str] = field(default_factory=list)
    weaknesses_introduced: list[str] = field(default_factory=list)
    strategy_before: str = ""
    strategy_after: str = ""
    scores_before: dict[str, float] = field(default_factory=dict)
    scores_after: dict[str, float] = field(default_factory=dict)


@dataclass
class RunProgression:
    """Full convergence run with all depths."""

    run_id: str
    task: str = ""
    task_type: str = ""
    depths: list[DepthSnapshot] = field(default_factory=list)
    satisfied: bool = False
    depth_reached: int = 0
    timestamp: str = ""


# --- Loading ---


def parse_depth(raw: dict) -> DepthSnapshot:
    """Parse a depth dict into a DepthSnapshot."""
    return DepthSnapshot(
        depth=raw.get("depth", 0),
        dimension_scores=dict(raw.get("dimension_scores", {})),
        weaknesses=list(raw.get("weaknesses", [])),
        strategy=raw.get("strategy", ""),
    )


def parse_run(raw: dict) -> RunProgression:
    """Parse a run dict into a RunProgression."""
    return RunProgression(
        run_id=raw.get("run_id", ""),
        task=raw.get("task", ""),
        task_type=raw.get("task_type", ""),
        depths=[parse_depth(d) for d in raw.get("depths", [])],
        satisfied=raw.get("satisfied", False),
        depth_reached=raw.get("depth_reached", 0),
        timestamp=raw.get("timestamp", ""),
    )


def load_run(run_id: str, jsonl_path: Optional[str] = None) -> Optional[RunProgression]:
    """Load a run by ID from a JSONL metrics file.

    Args:
        run_id: The run identifier to search for.
        jsonl_path: Path to the JSONL file. Defaults to data/converge-metrics.jsonl
                     relative to the repo root.

    Returns:
        RunProgression if found, None otherwise.
    """
    if jsonl_path is None:
        repo_root = Path(__file__).resolve().parent.parent
        jsonl_path = str(repo_root / "data" / "converge-metrics.jsonl")

    path = Path(jsonl_path)
    if not path.exists():
        return None

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if raw.get("run_id") == run_id:
                return parse_run(raw)

    return None


def load_all_runs(jsonl_path: Optional[str] = None) -> list[RunProgression]:
    """Load all runs from a JSONL metrics file."""
    if jsonl_path is None:
        repo_root = Path(__file__).resolve().parent.parent
        jsonl_path = str(repo_root / "data" / "converge-metrics.jsonl")

    path = Path(jsonl_path)
    if not path.exists():
        return []

    runs = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            runs.append(parse_run(raw))
    return runs


# --- Diffing ---


def diff_depths(run: RunProgression, depth_a: int, depth_b: int) -> Optional[DepthDiff]:
    """Compute diff between two depths in a run.

    Args:
        run: The convergence run.
        depth_a: The "before" depth index.
        depth_b: The "after" depth index.

    Returns:
        DepthDiff or None if depths not found.
    """
    snap_a = None
    snap_b = None
    for snap in run.depths:
        if snap.depth == depth_a:
            snap_a = snap
        if snap.depth == depth_b:
            snap_b = snap

    if snap_a is None or snap_b is None:
        return None

    # Dimension deltas -- union of all dimensions
    all_dims = sorted(set(snap_a.dimension_scores) | set(snap_b.dimension_scores))
    dimension_deltas = {}
    scores_before = {}
    scores_after = {}
    for dim in all_dims:
        before = snap_a.dimension_scores.get(dim, 0.0)
        after = snap_b.dimension_scores.get(dim, 0.0)
        scores_before[dim] = before
        scores_after[dim] = after
        dimension_deltas[dim] = after - before

    comp_a = snap_a.composite
    comp_b = snap_b.composite

    # Weakness tracking
    set_a = set(snap_a.weaknesses)
    set_b = set(snap_b.weaknesses)
    resolved = sorted(set_a - set_b)
    introduced = sorted(set_b - set_a)

    return DepthDiff(
        depth_before=depth_a,
        depth_after=depth_b,
        dimension_deltas=dimension_deltas,
        composite_before=comp_a,
        composite_after=comp_b,
        composite_delta=comp_b - comp_a,
        weaknesses_resolved=resolved,
        weaknesses_introduced=introduced,
        strategy_before=snap_a.strategy,
        strategy_after=snap_b.strategy,
        scores_before=scores_before,
        scores_after=scores_after,
    )


# --- Rendering ---


def _format_delta(val: float) -> str:
    """Format a delta value with directional indicator."""
    if val > 0:
        return f"+{val:g} \u25b2"
    elif val < 0:
        return f"{val:g} \u25bc"
    else:
        return "0 \u2500"


def _pad(text: str, width: int) -> str:
    """Pad text to width, accounting for Unicode characters."""
    # Each directional indicator is one character
    visible_len = len(text)
    padding = max(0, width - visible_len)
    return text + " " * padding


def render_diff(diff: DepthDiff) -> str:
    """Render a DepthDiff as an ASCII table.

    Returns a formatted string with box-drawing characters.
    """
    lines = []
    lines.append(f"Depth {diff.depth_before} \u2192 Depth {diff.depth_after}")

    # Compute column widths
    dims = sorted(diff.dimension_deltas.keys())
    col_dim = max(len("Dimension"), max((len(d) for d in dims), default=0), len("Composite"))
    col_before = max(len("Before"), 6)
    col_after = max(len("After"), 6)
    col_delta = max(len("Delta"), 8)

    # Widen columns for actual content
    for dim in dims:
        col_before = max(col_before, len(f"{diff.scores_before.get(dim, 0):g}"))
        col_after = max(col_after, len(f"{diff.scores_after.get(dim, 0):g}"))
        col_delta = max(col_delta, len(_format_delta(diff.dimension_deltas[dim])))

    col_before = max(col_before, len(f"{diff.composite_before:g}"))
    col_after = max(col_after, len(f"{diff.composite_after:g}"))
    col_delta = max(col_delta, len(_format_delta(diff.composite_delta)))

    def row(c1, c2, c3, c4, left, mid, right, horiz):
        return (
            f"{left} {_pad(c1, col_dim)} "
            f"{mid} {_pad(c2, col_before)} "
            f"{mid} {_pad(c3, col_after)} "
            f"{mid} {_pad(c4, col_delta)} {right}"
        )

    def separator(left, mid, right, horiz):
        return (
            f"{left}{horiz * (col_dim + 2)}"
            f"{mid}{horiz * (col_before + 2)}"
            f"{mid}{horiz * (col_after + 2)}"
            f"{mid}{horiz * (col_delta + 2)}{right}"
        )

    # Top border
    lines.append(separator("\u250c", "\u252c", "\u2510", "\u2500"))
    # Header
    lines.append(row("Dimension", "Before", "After", "Delta", "\u2502", "\u2502", "\u2502", ""))
    # Header separator
    lines.append(separator("\u251c", "\u253c", "\u2524", "\u2500"))

    # Dimension rows
    for dim in dims:
        before_str = f"{diff.scores_before.get(dim, 0):g}"
        after_str = f"{diff.scores_after.get(dim, 0):g}"
        delta_str = _format_delta(diff.dimension_deltas[dim])
        lines.append(row(dim, before_str, after_str, delta_str, "\u2502", "\u2502", "\u2502", ""))

    # Composite separator
    lines.append(separator("\u251c", "\u253c", "\u2524", "\u2500"))

    # Composite row
    comp_before = f"{diff.composite_before:g}"
    comp_after = f"{diff.composite_after:g}"
    comp_delta = _format_delta(diff.composite_delta)
    lines.append(row("Composite", comp_before, comp_after, comp_delta, "\u2502", "\u2502", "\u2502", ""))

    # Bottom border
    lines.append(separator("\u2514", "\u2534", "\u2518", "\u2500"))

    # Weaknesses
    resolved_str = ", ".join(diff.weaknesses_resolved) if diff.weaknesses_resolved else "none"
    introduced_str = ", ".join(diff.weaknesses_introduced) if diff.weaknesses_introduced else "none"
    lines.append(f"Weaknesses resolved: [{resolved_str}]")
    lines.append(f"Weaknesses introduced: [{introduced_str}]")

    # Strategy
    if diff.strategy_before or diff.strategy_after:
        lines.append(f'Strategy: "{diff.strategy_before}" \u2192 "{diff.strategy_after}"')

    return "\n".join(lines)


def render_all_depths(run: RunProgression) -> str:
    """Render progression through all depths in a run.

    Shows a diff table for each consecutive depth pair.
    """
    if len(run.depths) < 2:
        if len(run.depths) == 1:
            snap = run.depths[0]
            lines = [
                f"Run: {run.run_id} | Task: {run.task}",
                f"Single depth (depth {snap.depth}) -- no progression to show.",
                f"Composite: {snap.composite:g}",
            ]
            if snap.weaknesses:
                lines.append(f"Weaknesses: [{', '.join(snap.weaknesses)}]")
            if snap.strategy:
                lines.append(f'Strategy: "{snap.strategy}"')
            return "\n".join(lines)
        return f"Run: {run.run_id} | No depths recorded."

    sections = []
    header = f"Run: {run.run_id} | Task: {run.task} | Depths: {len(run.depths)}"
    if run.satisfied:
        header += " | SATISFIED"
    else:
        header += " | NOT SATISFIED"
    sections.append(header)
    sections.append("")

    sorted_depths = sorted(run.depths, key=lambda s: s.depth)
    for i in range(len(sorted_depths) - 1):
        d = diff_depths(run, sorted_depths[i].depth, sorted_depths[i + 1].depth)
        if d:
            sections.append(render_diff(d))
            sections.append("")

    return "\n".join(sections).rstrip()


def summarize_progression(run: RunProgression) -> str:
    """One-line summary of the run's journey.

    Format: <run_id> | <task> | D0(<comp>) -> D<n>(<comp>) | delta=+X | <satisfied>
    """
    if not run.depths:
        return f"{run.run_id} | {run.task} | no depths"

    sorted_depths = sorted(run.depths, key=lambda s: s.depth)
    first = sorted_depths[0]
    last = sorted_depths[-1]

    delta = last.composite - first.composite
    delta_str = f"+{delta:g}" if delta >= 0 else f"{delta:g}"
    satisfied_str = "SATISFIED" if run.satisfied else "NOT SATISFIED"

    if len(sorted_depths) == 1:
        return (
            f"{run.run_id} | {run.task} | "
            f"D{first.depth}({first.composite:g}) | single depth | {satisfied_str}"
        )

    return (
        f"{run.run_id} | {run.task} | "
        f"D{first.depth}({first.composite:g}) -> D{last.depth}({last.composite:g}) | "
        f"delta={delta_str} | {satisfied_str}"
    )


# --- CLI ---


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="converge-diff",
        description="Side-by-side comparison of convergence depth iterations.",
    )
    parser.add_argument(
        "--file",
        "-f",
        default=None,
        help="Path to JSONL metrics file (default: data/converge-metrics.jsonl)",
    )

    subparsers = parser.add_subparsers(dest="command")

    # diff command
    diff_parser = subparsers.add_parser("diff", help="Show depth diffs for a run")
    diff_parser.add_argument("run_id", help="Run identifier")
    diff_parser.add_argument("depth_a", nargs="?", type=int, default=None, help="Before depth")
    diff_parser.add_argument("depth_b", nargs="?", type=int, default=None, help="After depth")

    # summary command
    summary_parser = subparsers.add_parser("summary", help="One-line summary of a run")
    summary_parser.add_argument("run_id", help="Run identifier")

    # list command
    subparsers.add_parser("list", help="List all runs")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "list":
        runs = load_all_runs(args.file)
        if not runs:
            print("No runs found.")
            return 0
        for run in runs:
            print(summarize_progression(run))
        return 0

    # Commands that need a run_id
    run = load_run(args.run_id, args.file)
    if run is None:
        print(f"Run '{args.run_id}' not found.", file=sys.stderr)
        return 1

    if args.command == "summary":
        print(summarize_progression(run))
        return 0

    if args.command == "diff":
        if args.depth_a is not None and args.depth_b is not None:
            d = diff_depths(run, args.depth_a, args.depth_b)
            if d is None:
                print(
                    f"Depth {args.depth_a} or {args.depth_b} not found in run.",
                    file=sys.stderr,
                )
                return 1
            print(render_diff(d))
        else:
            print(render_all_depths(run))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
