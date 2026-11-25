#!/usr/bin/env python3
"""Convergence heatmap — visual performance analysis across time, genes, and depth.

Reads learning/converge-metrics.jsonl and renders ASCII heatmaps showing
where convergence performs best along three dimensions: time-of-day,
gene effectiveness, and depth distribution.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class HeatmapCell:
    """Single cell in a heatmap, tracking running average."""
    value: float = 0.0
    count: int = 0

    def add(self, v: float) -> None:
        self.value = (self.value * self.count + v) / (self.count + 1)
        self.count += 1


@dataclass
class Heatmap:
    """2D heatmap with labeled rows and columns."""
    rows: list[str]
    cols: list[str]
    cells: list[list[HeatmapCell]]
    title: str = ""

    @classmethod
    def create(cls, rows: list[str], cols: list[str], title: str = "") -> "Heatmap":
        cells = [[HeatmapCell() for _ in cols] for _ in rows]
        return cls(rows=rows, cols=cols, cells=cells, title=title)

    def get_cell(self, row_label: str, col_label: str) -> Optional[HeatmapCell]:
        try:
            r = self.rows.index(row_label)
            c = self.cols.index(col_label)
            return self.cells[r][c]
        except ValueError:
            return None


# --- Intensity rendering ---

INTENSITY_CHARS = " .:+#"


def intensity_char(value: float) -> str:
    """Map 0-100 value to ASCII intensity character."""
    if value <= 0:
        return INTENSITY_CHARS[0]
    idx = min(int(value / 20), len(INTENSITY_CHARS) - 1)
    return INTENSITY_CHARS[idx]


# --- Data loading ---

def load_runs(metrics_path: str) -> list[dict]:
    """Load converge-metrics.jsonl and collate events by run_id.

    Returns a list of run dicts, each containing merged fields from
    converge_start and converge_end events, plus a 'depths' list from
    converge_depth events.
    """
    events_by_run: dict[str, dict] = {}
    path = Path(metrics_path)
    if not path.exists():
        return []

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            run_id = entry.get("run_id")
            if not run_id:
                continue

            if run_id not in events_by_run:
                events_by_run[run_id] = {"run_id": run_id, "depths": []}

            event = entry.get("event", "")
            if event == "converge_start":
                events_by_run[run_id].update({
                    k: v for k, v in entry.items()
                    if k not in ("event",)
                })
            elif event == "converge_end":
                events_by_run[run_id].update({
                    k: v for k, v in entry.items()
                    if k not in ("event",)
                })
            elif event == "converge_depth":
                events_by_run[run_id]["depths"].append(entry)

    # Filter to runs that have a final_score (completed runs)
    runs = [r for r in events_by_run.values() if "final_score" in r]
    return runs


# --- Heatmap builders ---

TASK_TYPES = ["analysis", "code", "creative", "design", "general", "test"]
HOURS = [str(h) for h in range(24)]


def build_time_heatmap(runs: list[dict]) -> Heatmap:
    """Task type x hour-of-day heatmap. Cell = avg final score."""
    hmap = Heatmap.create(rows=TASK_TYPES, cols=HOURS, title="Convergence by Time of Day")

    for run in runs:
        task_type = run.get("task_type", "general")
        if task_type not in TASK_TYPES:
            task_type = "general"
        ts = run.get("timestamp")
        score = run.get("final_score")
        if ts is None or score is None:
            continue

        try:
            dt = datetime.fromisoformat(ts)
            hour = str(dt.hour)
        except (ValueError, TypeError):
            continue

        cell = hmap.get_cell(task_type, hour)
        if cell:
            cell.add(float(score))

    return hmap


def build_gene_heatmap(runs: list[dict]) -> Heatmap:
    """Task type x gene heatmap. Cell = avg score when gene was invoked."""
    # Collect all genes across runs
    all_genes: set[str] = set()
    for run in runs:
        for g in run.get("invoked_genes", []):
            all_genes.add(g)
        for g in run.get("expressed_genes", []):
            all_genes.add(g)

    if not all_genes:
        return Heatmap.create(rows=TASK_TYPES, cols=["(no genes)"],
                              title="Gene Effectiveness (no gene data)")

    genes_sorted = sorted(all_genes)
    hmap = Heatmap.create(rows=TASK_TYPES, cols=genes_sorted,
                          title="Gene Effectiveness by Task Type")

    for run in runs:
        task_type = run.get("task_type", "general")
        if task_type not in TASK_TYPES:
            task_type = "general"
        score = run.get("final_score")
        if score is None:
            continue

        run_genes = set(run.get("invoked_genes", []) + run.get("expressed_genes", []))
        for gene in run_genes:
            cell = hmap.get_cell(task_type, gene)
            if cell:
                cell.add(float(score))

    return hmap


def build_depth_heatmap(runs: list[dict]) -> Heatmap:
    """Depth level x task type heatmap. Cell = avg score at that depth."""
    # Determine max depth from data
    max_depth = 0
    for run in runs:
        for d in run.get("depths", []):
            depth = d.get("depth", 0)
            if depth > max_depth:
                max_depth = depth

    if max_depth == 0 and runs:
        # Fallback: use depth_reached
        for run in runs:
            dr = run.get("depth_reached", 0)
            if dr > max_depth:
                max_depth = dr

    depth_labels = [f"d{i}" for i in range(max_depth + 1)]
    if not depth_labels:
        depth_labels = ["d0"]

    hmap = Heatmap.create(rows=depth_labels, cols=TASK_TYPES,
                          title="Score Distribution by Depth")

    for run in runs:
        task_type = run.get("task_type", "general")
        if task_type not in TASK_TYPES:
            task_type = "general"

        for d_entry in run.get("depths", []):
            depth = d_entry.get("depth", 0)
            score = d_entry.get("score")
            if score is None:
                continue
            label = f"d{depth}"
            cell = hmap.get_cell(label, task_type)
            if cell:
                cell.add(float(score))

    return hmap


# --- Rendering ---

def format_heatmap(heatmap: Heatmap) -> str:
    """Render a Heatmap as ASCII art with intensity characters."""
    if not heatmap.rows or not heatmap.cols:
        return f"{heatmap.title}\n(no data)"

    lines: list[str] = []
    if heatmap.title:
        lines.append(heatmap.title)
        lines.append("=" * len(heatmap.title))
        lines.append("")

    # Calculate label widths
    row_width = max(len(r) for r in heatmap.rows)
    col_width = max(max(len(c) for c in heatmap.cols), 3)

    # Legend
    lines.append(f"{'Intensity:':<{row_width}}  "
                 f"' '=0-20  '.'=20-40  ':'=40-60  '+'=60-80  '#'=80-100")
    lines.append("")

    # Column headers
    header = " " * (row_width + 2)
    for col in heatmap.cols:
        header += col[:col_width].center(col_width)
    lines.append(header)

    # Separator
    lines.append(" " * (row_width + 2) + "-" * (col_width * len(heatmap.cols)))

    # Rows
    for r_idx, row_label in enumerate(heatmap.rows):
        row_str = row_label.ljust(row_width) + " |"
        for c_idx in range(len(heatmap.cols)):
            cell = heatmap.cells[r_idx][c_idx]
            if cell.count == 0:
                ch = " "
            else:
                ch = intensity_char(cell.value)
            row_str += ch.center(col_width)
        lines.append(row_str)

    # Summary stats
    lines.append("")
    total_cells = sum(
        1 for row in heatmap.cells for cell in row if cell.count > 0
    )
    total_samples = sum(
        cell.count for row in heatmap.cells for cell in row
    )
    lines.append(f"Cells with data: {total_cells}/{len(heatmap.rows) * len(heatmap.cols)}  "
                 f"Total samples: {total_samples}")

    return "\n".join(lines)


# --- Peak performance ---

def peak_performance(runs: list[dict]) -> dict:
    """Identify optimal convergence conditions from run data."""
    if not runs:
        return {"best_hour": None, "best_task_type": None, "best_genes": [],
                "highest_score": 0, "total_runs": 0}

    # Best hour
    hour_scores: dict[int, list[float]] = defaultdict(list)
    task_scores: dict[str, list[float]] = defaultdict(list)
    gene_scores: dict[str, list[float]] = defaultdict(list)

    for run in runs:
        score = run.get("final_score")
        if score is None:
            continue
        score = float(score)

        # Hour
        ts = run.get("timestamp")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                hour_scores[dt.hour].append(score)
            except (ValueError, TypeError):
                pass

        # Task type
        task_type = run.get("task_type", "general")
        task_scores[task_type].append(score)

        # Genes
        run_genes = set(run.get("invoked_genes", []) + run.get("expressed_genes", []))
        for gene in run_genes:
            gene_scores[gene].append(score)

    def best_avg(d: dict) -> tuple:
        if not d:
            return (None, 0.0)
        best_key = max(d, key=lambda k: sum(d[k]) / len(d[k]))
        return (best_key, sum(d[best_key]) / len(d[best_key]))

    best_hour, best_hour_avg = best_avg(hour_scores)
    best_task, best_task_avg = best_avg(task_scores)

    # Top 5 genes by avg score (min 2 samples)
    qualified_genes = {k: v for k, v in gene_scores.items() if len(v) >= 2}
    if qualified_genes:
        sorted_genes = sorted(qualified_genes,
                              key=lambda k: sum(qualified_genes[k]) / len(qualified_genes[k]),
                              reverse=True)[:5]
        best_genes = [
            {"gene": g,
             "avg_score": round(sum(gene_scores[g]) / len(gene_scores[g]), 1),
             "sample_count": len(gene_scores[g])}
            for g in sorted_genes
        ]
    else:
        # Fall back to any genes
        sorted_genes = sorted(gene_scores,
                              key=lambda k: sum(gene_scores[k]) / len(gene_scores[k]),
                              reverse=True)[:5]
        best_genes = [
            {"gene": g,
             "avg_score": round(sum(gene_scores[g]) / len(gene_scores[g]), 1),
             "sample_count": len(gene_scores[g])}
            for g in sorted_genes
        ]

    highest = max((r.get("final_score", 0) for r in runs), default=0)

    return {
        "best_hour": best_hour,
        "best_hour_avg": round(best_hour_avg, 1) if best_hour is not None else None,
        "best_task_type": best_task,
        "best_task_avg": round(best_task_avg, 1) if best_task else None,
        "best_genes": best_genes,
        "highest_score": highest,
        "total_runs": len(runs),
    }


def format_peaks(peaks: dict) -> str:
    """Human-readable peak performance summary."""
    lines = [
        "Peak Convergence Conditions",
        "===========================",
        "",
        f"Total completed runs: {peaks['total_runs']}",
        f"Highest single score: {peaks['highest_score']}",
        "",
    ]

    if peaks["best_hour"] is not None:
        lines.append(f"Best hour: {peaks['best_hour']:02d}:00 "
                     f"(avg {peaks['best_hour_avg']})")
    if peaks["best_task_type"]:
        lines.append(f"Best task type: {peaks['best_task_type']} "
                     f"(avg {peaks['best_task_avg']})")

    if peaks["best_genes"]:
        lines.append("")
        lines.append("Top genes:")
        for g in peaks["best_genes"]:
            lines.append(f"  {g['gene']}: avg {g['avg_score']} "
                         f"({g['sample_count']} runs)")

    return "\n".join(lines)


# --- CLI ---

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convergence performance heatmaps"
    )
    parser.add_argument(
        "command",
        choices=["time", "genes", "depth", "peaks"],
        help="Heatmap type: time | genes | depth | peaks"
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Output as JSON instead of ASCII"
    )
    parser.add_argument(
        "--metrics", default=None,
        help="Path to converge-metrics.jsonl"
    )

    args = parser.parse_args()

    # Resolve metrics path
    if args.metrics:
        metrics_path = args.metrics
    else:
        claude_dir = os.environ.get("CLAUDE_DIR", os.path.expanduser("~/.claude"))
        metrics_path = os.path.join(claude_dir, "learning", "converge-metrics.jsonl")

    runs = load_runs(metrics_path)
    if not runs:
        print(f"No completed runs found in {metrics_path}", file=sys.stderr)
        sys.exit(1)

    if args.command == "time":
        hmap = build_time_heatmap(runs)
        if args.as_json:
            print(json.dumps(_heatmap_to_dict(hmap), indent=2))
        else:
            print(format_heatmap(hmap))

    elif args.command == "genes":
        hmap = build_gene_heatmap(runs)
        if args.as_json:
            print(json.dumps(_heatmap_to_dict(hmap), indent=2))
        else:
            print(format_heatmap(hmap))

    elif args.command == "depth":
        hmap = build_depth_heatmap(runs)
        if args.as_json:
            print(json.dumps(_heatmap_to_dict(hmap), indent=2))
        else:
            print(format_heatmap(hmap))

    elif args.command == "peaks":
        peaks = peak_performance(runs)
        if args.as_json:
            print(json.dumps(peaks, indent=2))
        else:
            print(format_peaks(peaks))


def _heatmap_to_dict(hmap: Heatmap) -> dict:
    """Serialize heatmap to JSON-friendly dict."""
    return {
        "title": hmap.title,
        "rows": hmap.rows,
        "cols": hmap.cols,
        "cells": [
            [{"value": round(c.value, 1), "count": c.count} for c in row]
            for row in hmap.cells
        ],
    }


if __name__ == "__main__":
    main()
