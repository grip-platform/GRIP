"""Convergence replay animator -- step-through run visualisation.

Loads convergence run data from JSONL metrics files and renders
frame-by-frame ASCII bar chart visualisations showing dimension
evolution across depths.

Usage:
    python -m lib.converge_replay <run_id> [--mode step|film|summary] [--metrics PATH]
    python -m lib.converge_replay latest [--mode film]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_METRICS_PATH = Path("cache/converge-metrics.jsonl")
BAR_WIDTH = 20


@dataclass
class Frame:
    """A single depth snapshot in a convergence run."""

    depth: int
    dimension_scores: dict[str, int | float]
    composite: float
    weaknesses: list[str]
    strategy: str
    delta_from_prev: dict[str, float] = field(default_factory=dict)
    total_depths: int = 0


def load_run(run_id: str, metrics_path: Path | str = DEFAULT_METRICS_PATH) -> dict[str, Any] | None:
    """Load a specific run from JSONL metrics file."""
    metrics_path = Path(metrics_path)
    if not metrics_path.exists():
        return None
    with metrics_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("run_id") == run_id:
                return record
    return None


def find_latest_run(metrics_path: Path | str = DEFAULT_METRICS_PATH) -> str | None:
    """Return the run_id of the most recent entry in the JSONL file."""
    metrics_path = Path(metrics_path)
    if not metrics_path.exists():
        return None
    latest = None
    with metrics_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "run_id" in record:
                latest = record["run_id"]
    return latest


def _compute_composite(scores: dict[str, int | float]) -> float:
    """Compute arithmetic mean of dimension scores."""
    if not scores:
        return 0.0
    return round(sum(scores.values()) / len(scores), 1)


def build_frames(run: dict[str, Any]) -> list[Frame]:
    """Convert a run's depth entries into replay frames."""
    depths = run.get("depths", [])
    if not depths:
        return []

    total = len(depths)
    frames: list[Frame] = []
    prev_scores: dict[str, int | float] = {}
    prev_composite = 0.0

    for entry in depths:
        scores = entry.get("dimension_scores", {})
        composite = _compute_composite(scores)

        delta: dict[str, float] = {}
        if prev_scores:
            for dim, val in scores.items():
                prev_val = prev_scores.get(dim, 0)
                delta[dim] = round(val - prev_val, 1)
            delta["_composite"] = round(composite - prev_composite, 1)

        frame = Frame(
            depth=entry.get("depth", 0),
            dimension_scores=dict(scores),
            composite=composite,
            weaknesses=list(entry.get("weaknesses", [])),
            strategy=entry.get("strategy", ""),
            delta_from_prev=delta,
            total_depths=total,
        )
        frames.append(frame)
        prev_scores = dict(scores)
        prev_composite = composite

    return frames


def _render_bar(value: int | float, width: int = BAR_WIDTH) -> str:
    """Render an ASCII bar: filled blocks + empty blocks."""
    clamped = max(0, min(100, float(value)))
    filled = round(clamped / 100 * width)
    empty = width - filled
    return "\u2588" * filled + "\u2591" * empty


def _format_delta(delta: float) -> str:
    """Format a delta value with sign."""
    if delta > 0:
        return f" (+{delta:g})"
    elif delta < 0:
        return f" ({delta:g})"
    return ""


def render_frame(frame: Frame, width: int = 60) -> str:
    """Render a single frame with ASCII bar chart for dimensions."""
    inner = width - 2  # subtract the two ║ border chars

    # Header
    depth_label = f" Depth {frame.depth}/{frame.total_depths - 1} " if frame.total_depths > 1 else f" Depth {frame.depth} "
    header_fill = inner - len(depth_label) - 2  # 2 for the ══ prefix
    header = "\u2554" + "\u2550" * 2 + depth_label + "\u2550" * max(0, header_fill) + "\u2557"

    lines = [header]

    # Dimension bars
    if frame.dimension_scores:
        max_dim_len = max(len(d) for d in frame.dimension_scores)
    else:
        max_dim_len = 0

    for dim, score in frame.dimension_scores.items():
        bar = _render_bar(score)
        delta_str = _format_delta(frame.delta_from_prev.get(dim, 0)) if frame.delta_from_prev else ""
        content = f" {dim:{max_dim_len}s}: {bar} {score:g}{delta_str}"
        padded = content.ljust(inner)
        lines.append(f"\u2551{padded}\u2551")

    # Separator
    sep = " " + "\u2500" * (inner - 2) + " "
    lines.append(f"\u2551{sep}\u2551")

    # Composite
    comp_delta = _format_delta(frame.delta_from_prev.get("_composite", 0)) if frame.delta_from_prev else ""
    comp_content = f" Composite: {frame.composite}{comp_delta}"
    lines.append(f"\u2551{comp_content.ljust(inner)}\u2551")

    # Weaknesses
    if frame.weaknesses:
        weak_str = ", ".join(frame.weaknesses)
    else:
        weak_str = "[none]"
    weak_content = f" Weaknesses: {weak_str}"
    lines.append(f"\u2551{weak_content.ljust(inner)}\u2551")

    # Strategy
    strat_content = f" Strategy: {frame.strategy}" if frame.strategy else " Strategy: [none]"
    lines.append(f"\u2551{strat_content.ljust(inner)}\u2551")

    # Footer
    footer = "\u255a" + "\u2550" * inner + "\u255d"
    lines.append(footer)

    return "\n".join(lines)


def render_film(frames: list[Frame], width: int = 60) -> str:
    """Render all frames in sequence with separators."""
    if not frames:
        return "[no frames to display]"
    parts: list[str] = []
    for i, frame in enumerate(frames):
        parts.append(render_frame(frame, width))
        if i < len(frames) - 1:
            parts.append("")  # blank line separator
    return "\n".join(parts)


def render_summary(frames: list[Frame], width: int = 60) -> str:
    """Render a compact single-frame overview showing start to end journey."""
    if not frames:
        return "[no frames to display]"

    first = frames[0]
    last = frames[-1]
    inner = width - 2

    header_label = f" Summary: {len(frames)} depths "
    header_fill = inner - len(header_label) - 2
    header = "\u2554" + "\u2550" * 2 + header_label + "\u2550" * max(0, header_fill) + "\u2557"

    lines = [header]

    # Collect all dimensions (union of first and last)
    all_dims = list(dict.fromkeys(list(first.dimension_scores.keys()) + list(last.dimension_scores.keys())))

    if all_dims:
        max_dim_len = max(len(d) for d in all_dims)
    else:
        max_dim_len = 0

    for dim in all_dims:
        start_val = first.dimension_scores.get(dim, 0)
        end_val = last.dimension_scores.get(dim, 0)
        bar = _render_bar(end_val)
        delta = round(end_val - start_val, 1)
        delta_str = _format_delta(delta)
        content = f" {dim:{max_dim_len}s}: {bar} {start_val:g} -> {end_val:g}{delta_str}"
        padded = content.ljust(inner)
        lines.append(f"\u2551{padded}\u2551")

    # Separator
    sep = " " + "\u2500" * (inner - 2) + " "
    lines.append(f"\u2551{sep}\u2551")

    # Composite
    first_comp = first.composite
    last_comp = last.composite
    comp_delta = round(last_comp - first_comp, 1)
    comp_delta_str = _format_delta(comp_delta)
    comp_content = f" Composite: {first_comp} -> {last_comp}{comp_delta_str}"
    lines.append(f"\u2551{comp_content.ljust(inner)}\u2551")

    # Final state
    if last.weaknesses:
        weak_str = ", ".join(last.weaknesses)
    else:
        weak_str = "[none]"
    weak_content = f" Final weaknesses: {weak_str}"
    lines.append(f"\u2551{weak_content.ljust(inner)}\u2551")

    footer = "\u255a" + "\u2550" * inner + "\u255d"
    lines.append(footer)

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="converge-replay",
        description="Step-through replay of convergence runs",
    )
    parser.add_argument(
        "run_id",
        help="Run ID to replay, or 'latest' for most recent",
    )
    parser.add_argument(
        "--mode",
        choices=["step", "film", "summary"],
        default="step",
        help="Display mode (default: step)",
    )
    parser.add_argument(
        "--metrics",
        default=str(DEFAULT_METRICS_PATH),
        help=f"Path to metrics JSONL file (default: {DEFAULT_METRICS_PATH})",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=60,
        help="Frame width in characters (default: 60)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    metrics_path = Path(args.metrics)

    # Resolve run_id
    run_id = args.run_id
    if run_id == "latest":
        run_id = find_latest_run(metrics_path)
        if run_id is None:
            print("No runs found.", file=sys.stderr)
            return 1

    # Load run
    run = load_run(run_id, metrics_path)
    if run is None:
        print(f"Run '{run_id}' not found.", file=sys.stderr)
        return 1

    # Build frames
    frames = build_frames(run)
    if not frames:
        print(f"Run '{run_id}' has no depth data.", file=sys.stderr)
        return 1

    # Render
    if args.mode == "film":
        print(render_film(frames, args.width))
    elif args.mode == "summary":
        print(render_summary(frames, args.width))
    else:
        # Step mode: one frame at a time
        for i, frame in enumerate(frames):
            print(render_frame(frame, args.width))
            if i < len(frames) - 1:
                try:
                    input("\n[Enter for next depth, Ctrl+C to quit]\n")
                except (KeyboardInterrupt, EOFError):
                    break

    return 0


if __name__ == "__main__":
    sys.exit(main())
