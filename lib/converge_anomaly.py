#!/usr/bin/env python3
"""
converge_anomaly.py -- Detect unusual convergence patterns.

Scans converge-metrics.jsonl for scoring problems, attractor patterns,
and genuine convergence failures that should be flagged for review.

Usage:
    python3 lib/converge_anomaly.py scan
    python3 lib/converge_anomaly.py scan --severity high
    python3 lib/converge_anomaly.py scan --json
    python3 lib/converge_anomaly.py check RUN_ID
    python3 lib/converge_anomaly.py check RUN_ID --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_METRICS = _ROOT / "learning" / "converge-metrics.jsonl"

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass
class Anomaly:
    run_id: str
    anomaly_type: str
    severity: str  # low | medium | high
    description: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnomalyReport:
    anomalies: list[Anomaly] = field(default_factory=list)
    total_runs_scanned: int = 0
    anomaly_rate: float = 0.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_metrics(path: str | Path = _DEFAULT_METRICS) -> list[dict]:
    """Load and parse converge-metrics.jsonl, returning grouped runs.

    Returns a list of dicts, each representing a single convergence run
    with keys: run_id, start, depths (list sorted by depth), end.
    """
    path = Path(path)
    if not path.exists():
        return []

    events: dict[str, dict] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = evt.get("run_id")
            if not rid:
                continue
            if rid not in events:
                events[rid] = {"run_id": rid, "start": None, "depths": [], "end": None}
            event_type = evt.get("event")
            if event_type == "converge_start":
                events[rid]["start"] = evt
            elif event_type == "converge_depth":
                events[rid]["depths"].append(evt)
            elif event_type == "converge_end":
                events[rid]["end"] = evt

    runs = []
    for rid, data in events.items():
        data["depths"].sort(key=lambda d: d.get("depth", 0))
        runs.append(data)
    return runs


# ---------------------------------------------------------------------------
# Anomaly checks
# ---------------------------------------------------------------------------

def _check_impossible_scores(rid: str, depths: list[dict], end: Optional[dict]) -> list[Anomaly]:
    """Detect scores outside [0, 100]."""
    anomalies: list[Anomaly] = []
    for d in depths:
        s = d.get("score")
        if s is not None and (s > 100 or s < 0):
            anomalies.append(Anomaly(rid, "IMPOSSIBLE_SCORE", "high",
                f"Score {s} at depth {d.get('depth')} is outside valid range [0, 100]",
                {"score": s, "depth": d.get("depth")}))
    if end:
        fs = end.get("final_score")
        if fs is not None and (fs > 100 or fs < 0):
            anomalies.append(Anomaly(rid, "IMPOSSIBLE_SCORE", "high",
                f"Final score {fs} is outside valid range [0, 100]",
                {"score": fs, "depth": "final"}))
    return anomalies


def _check_score_trajectory(rid: str, scores: list[float]) -> list[Anomaly]:
    """Detect sudden drops, velocity spikes, and suspicious plateaus."""
    anomalies: list[Anomaly] = []
    for i in range(1, len(scores)):
        delta = scores[i] - scores[i - 1]
        if delta < -15:
            anomalies.append(Anomaly(rid, "SUDDEN_DROP", "high",
                f"Score dropped {abs(delta):.1f} points from depth {i-1} to {i}",
                {"depth": i, "score_before": scores[i - 1], "score_after": scores[i], "delta": delta}))
        if delta > 30:
            anomalies.append(Anomaly(rid, "VELOCITY_SPIKE", "medium",
                f"Score jumped {delta:.1f} points at depth {i} (likely scoring error)",
                {"depth": i, "velocity": delta}))
    if len(scores) >= 2:
        score_range = max(scores) - min(scores)
        if score_range <= 2:
            anomalies.append(Anomaly(rid, "SUSPICIOUS_PLATEAU", "medium",
                f"All {len(scores)} depth scores within {score_range:.1f} points (range: {min(scores):.1f}-{max(scores):.1f})",
                {"scores": scores, "range": score_range}))
    return anomalies


def _check_dimension_collapse(rid: str, depths: list[dict]) -> list[Anomaly]:
    """Detect uniform dimension scores (stdev <= 0.5)."""
    anomalies: list[Anomaly] = []
    for d in depths:
        dim_scores = d.get("dimension_scores")
        if dim_scores and len(dim_scores) >= 2:
            vals = list(dim_scores.values())
            if all(isinstance(v, (int, float)) for v in vals):
                stdev = statistics.pstdev(vals)
                if stdev <= 0.5:
                    anomalies.append(Anomaly(rid, "DIMENSION_COLLAPSE", "high",
                        f"All dimensions within 1 point at depth {d.get('depth')} (stdev={stdev:.2f})",
                        {"dimension_scores": dim_scores, "stdev": stdev}))
    return anomalies


def _check_outcome(
    rid: str, start: Optional[dict], end: Optional[dict], scores: list[float],
) -> list[Anomaly]:
    """Detect stalls, regressions, and missing dimensions."""
    anomalies: list[Anomaly] = []
    if end and start:
        max_depth = start.get("max_depth")
        depth_reached = end.get("depth_reached")
        final_score = end.get("final_score")
        if (max_depth is not None and depth_reached is not None
                and final_score is not None
                and depth_reached >= max_depth and final_score < 70):
            anomalies.append(Anomaly(rid, "STALL_AT_MAX", "low",
                f"Reached max depth {max_depth} with final score {final_score:.1f} (<70)",
                {"final_score": final_score, "max_depth": max_depth}))
    if end and scores:
        initial, final = scores[0], end.get("final_score")
        if final is not None and final < initial:
            anomalies.append(Anomaly(rid, "REGRESSION", "high",
                f"Final score ({final:.1f}) lower than initial ({initial:.1f}), delta={final - initial:.1f}",
                {"initial": initial, "final": final, "delta": final - initial}))
    return anomalies


def _check_missing_dimensions(rid: str, depths: list[dict]) -> list[Anomaly]:
    """Flag depths recorded without dimension_scores."""
    return [
        Anomaly(rid, "MISSING_DIMENSIONS", "low",
                f"Depth {d.get('depth')} has no dimension_scores",
                {"depth": d.get("depth"), "run_id": rid})
        for d in depths if not d.get("dimension_scores")
    ]


def check_run(run: dict) -> list[Anomaly]:
    """Check a single run for all anomaly types."""
    rid = run.get("run_id", "unknown")
    depths = run.get("depths", [])
    end = run.get("end")
    start = run.get("start")
    scores = [d.get("score", 0) for d in depths if d.get("score") is not None]

    anomalies: list[Anomaly] = []
    anomalies.extend(_check_impossible_scores(rid, depths, end))
    anomalies.extend(_check_score_trajectory(rid, scores))
    anomalies.extend(_check_dimension_collapse(rid, depths))
    anomalies.extend(_check_outcome(rid, start, end, scores))
    anomalies.extend(_check_missing_dimensions(rid, depths))
    return anomalies


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def detect_anomalies(runs: list[dict]) -> AnomalyReport:
    """Scan all runs for anomalies and return a report."""
    all_anomalies: list[Anomaly] = []
    for run in runs:
        all_anomalies.extend(check_run(run))

    runs_with_anomalies = len({a.run_id for a in all_anomalies})
    total = len(runs)
    rate = runs_with_anomalies / total if total > 0 else 0.0

    return AnomalyReport(
        anomalies=all_anomalies,
        total_runs_scanned=total,
        anomaly_rate=rate,
    )


def severity_filter(
    anomalies: list[Anomaly],
    min_severity: str = "low",
) -> list[Anomaly]:
    """Filter anomalies to only those at or above min_severity."""
    threshold = SEVERITY_ORDER.get(min_severity, 0)
    return [a for a in anomalies if SEVERITY_ORDER.get(a.severity, 0) >= threshold]


def format_report(report: AnomalyReport) -> str:
    """Human-readable anomaly report with summary table."""
    lines: list[str] = []
    lines.append("Convergence Anomaly Report")
    lines.append("=" * 40)
    lines.append(f"Runs scanned: {report.total_runs_scanned}")
    lines.append(f"Anomaly rate: {report.anomaly_rate:.0%} of runs have anomalies")
    lines.append(f"Total anomalies: {len(report.anomalies)}")
    lines.append("")

    if not report.anomalies:
        lines.append("No anomalies detected.")
        return "\n".join(lines)

    # Count by type
    type_counts: dict[str, int] = {}
    for a in report.anomalies:
        type_counts[a.anomaly_type] = type_counts.get(a.anomaly_type, 0) + 1

    lines.append("By type:")
    type_col = max(len(t) for t in type_counts) + 2
    for atype, count in sorted(type_counts.items()):
        lines.append(f"  {atype:<{type_col}} {count}")
    lines.append("")

    # Count by severity
    sev_counts: dict[str, int] = {}
    for a in report.anomalies:
        sev_counts[a.severity] = sev_counts.get(a.severity, 0) + 1

    lines.append("By severity:")
    for sev in ("high", "medium", "low"):
        if sev in sev_counts:
            lines.append(f"  {sev:<10} {sev_counts[sev]}")
    lines.append("")

    # Detail table
    lines.append("Details:")
    lines.append("-" * 40)
    for a in report.anomalies:
        lines.append(f"[{a.severity.upper()}] {a.anomaly_type} (run: {a.run_id})")
        lines.append(f"  {a.description}")
        if a.evidence:
            evidence_str = json.dumps(a.evidence, default=str)
            if len(evidence_str) > 120:
                evidence_str = evidence_str[:117] + "..."
            lines.append(f"  Evidence: {evidence_str}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="converge_anomaly",
        description="Detect anomalous convergence patterns",
    )
    sub = parser.add_subparsers(dest="command")

    scan_p = sub.add_parser("scan", help="Scan all runs for anomalies")
    scan_p.add_argument("--severity", choices=["low", "medium", "high"], default="low")
    scan_p.add_argument("--json", action="store_true", dest="json_output")
    scan_p.add_argument("--path", type=str, default=None, help="Path to metrics JSONL")

    check_p = sub.add_parser("check", help="Check a specific run")
    check_p.add_argument("run_id", type=str)
    check_p.add_argument("--json", action="store_true", dest="json_output")
    check_p.add_argument("--path", type=str, default=None, help="Path to metrics JSONL")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    metrics_path = args.path or _DEFAULT_METRICS
    runs = load_metrics(metrics_path)

    if args.command == "scan":
        report = detect_anomalies(runs)
        report.anomalies = severity_filter(report.anomalies, args.severity)
        if args.json_output:
            out = {
                "total_runs_scanned": report.total_runs_scanned,
                "anomaly_rate": report.anomaly_rate,
                "anomalies": [asdict(a) for a in report.anomalies],
            }
            print(json.dumps(out, indent=2, default=str))
        else:
            print(format_report(report))
        return 0

    elif args.command == "check":
        target = [r for r in runs if r["run_id"] == args.run_id]
        if not target:
            print(f"Run {args.run_id} not found in metrics.", file=sys.stderr)
            return 1
        anomalies = check_run(target[0])
        if args.json_output:
            print(json.dumps([asdict(a) for a in anomalies], indent=2, default=str))
        else:
            if not anomalies:
                print(f"Run {args.run_id}: no anomalies detected.")
            else:
                for a in anomalies:
                    print(f"[{a.severity.upper()}] {a.anomaly_type}: {a.description}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
