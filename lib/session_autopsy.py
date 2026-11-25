"""Session Autopsy -- Post-mortem analysis of failed/stalled convergence sessions.

Analyzes converge run data to identify why sessions failed, stalled, or
produced suboptimal results. Generates actionable recommendations.

Usage:
    python3 -m lib.session_autopsy autopsy <run_id>
    python3 -m lib.session_autopsy autopsy latest
    python3 -m lib.session_autopsy autopsy --failed-only
    python3 -m lib.session_autopsy summary
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class FailureCause(Enum):
    """Root causes of session failure."""

    CONTEXT_EXHAUSTION = "context_exhaustion"
    ERROR_CASCADE = "error_cascade"
    DRIFT_ACCUMULATION = "drift_accumulation"
    WRONG_STRATEGY = "wrong_strategy"
    STALL = "stall"
    REGRESSION = "regression"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class CauseEvidence:
    """Evidence linking a failure cause to observed behavior."""

    cause: FailureCause
    confidence: float  # 0.0 - 1.0
    evidence: str
    depth_at_failure: int | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be 0-1, got {self.confidence}")


@dataclass
class AutopsyReport:
    """Complete post-mortem report for a failed/stalled run."""

    run_id: str
    task: str
    causes: list[CauseEvidence] = field(default_factory=list)
    timeline: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    primary_cause: FailureCause = FailureCause.UNKNOWN
    was_preventable: bool = False


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def detect_context_exhaustion(run: dict[str, Any]) -> CauseEvidence | None:
    """Check if the run hit max depth without satisfying the criterion."""
    depth_reached = run.get("depth_reached", 0)
    max_depth = run.get("max_depth", 0)
    satisfied = run.get("satisfied", False)

    if max_depth <= 0:
        return None

    if depth_reached >= max_depth and not satisfied:
        ratio = depth_reached / max_depth
        confidence = min(1.0, 0.6 + ratio * 0.4)
        return CauseEvidence(
            cause=FailureCause.CONTEXT_EXHAUSTION,
            confidence=round(confidence, 2),
            evidence=(
                f"Reached max depth {depth_reached}/{max_depth} "
                f"without satisfying criterion"
            ),
            depth_at_failure=depth_reached,
        )
    return None


def detect_error_cascade(run: dict[str, Any]) -> CauseEvidence | None:
    """Check if multiple dimensions regressed simultaneously."""
    depths = run.get("depths", [])
    if len(depths) < 2:
        return None

    worst_cascade_depth = None
    worst_regressed_count = 0

    for i in range(1, len(depths)):
        prev_scores = depths[i - 1].get("dimension_scores", {})
        curr_scores = depths[i].get("dimension_scores", {})

        if not prev_scores or not curr_scores:
            continue

        common_dims = set(prev_scores.keys()) & set(curr_scores.keys())
        regressed = [
            d for d in common_dims
            if curr_scores[d] < prev_scores[d]
        ]

        if len(regressed) >= 2 and len(regressed) > worst_regressed_count:
            worst_regressed_count = len(regressed)
            worst_cascade_depth = i

    if worst_cascade_depth is not None:
        total_dims = len(
            depths[worst_cascade_depth].get("dimension_scores", {})
        )
        fraction = worst_regressed_count / max(total_dims, 1)
        confidence = min(1.0, 0.5 + fraction * 0.5)
        return CauseEvidence(
            cause=FailureCause.ERROR_CASCADE,
            confidence=round(confidence, 2),
            evidence=(
                f"{worst_regressed_count} dimensions regressed simultaneously "
                f"at depth {worst_cascade_depth}"
            ),
            depth_at_failure=worst_cascade_depth,
        )
    return None


def detect_drift(run: dict[str, Any]) -> CauseEvidence | None:
    """Check if strategy changed too frequently without improvement."""
    depths = run.get("depths", [])
    if len(depths) < 3:
        return None

    strategies = [d.get("strategy", "") for d in depths]
    changes = sum(
        1 for i in range(1, len(strategies))
        if strategies[i] and strategies[i] != strategies[i - 1]
    )

    if changes < 2:
        return None

    # Check if changes led to improvement
    first_scores = depths[0].get("dimension_scores", {})
    last_scores = depths[-1].get("dimension_scores", {})

    if not first_scores or not last_scores:
        return None

    common = set(first_scores.keys()) & set(last_scores.keys())
    if not common:
        return None

    avg_first = sum(first_scores[d] for d in common) / len(common)
    avg_last = sum(last_scores[d] for d in common) / len(common)
    improved = avg_last > avg_first

    if not improved:
        change_rate = changes / (len(depths) - 1)
        confidence = min(1.0, 0.4 + change_rate * 0.5)
        return CauseEvidence(
            cause=FailureCause.DRIFT_ACCUMULATION,
            confidence=round(confidence, 2),
            evidence=(
                f"Strategy changed {changes} times across {len(depths)} depths "
                f"without net improvement (avg {avg_first:.1f} -> {avg_last:.1f})"
            ),
            depth_at_failure=len(depths) - 1,
        )
    return None


def detect_wrong_strategy(run: dict[str, Any]) -> CauseEvidence | None:
    """Check if initial strategy was suboptimal based on early performance."""
    depths = run.get("depths", [])
    if len(depths) < 2:
        return None

    task_type = run.get("task_type", "")
    first = depths[0]
    first_scores = first.get("dimension_scores", {})
    first_weaknesses = first.get("weaknesses", [])

    if not first_scores:
        return None

    avg_first = sum(first_scores.values()) / len(first_scores)

    # Low initial scores suggest wrong approach from the start
    if avg_first < 40:
        confidence = min(1.0, 0.3 + (40 - avg_first) / 80)
        detail = ""
        if first_weaknesses:
            detail = f" Initial weaknesses: {', '.join(first_weaknesses[:3])}"
        if task_type:
            detail += f" Task type: {task_type}"

        return CauseEvidence(
            cause=FailureCause.WRONG_STRATEGY,
            confidence=round(confidence, 2),
            evidence=(
                f"Very low initial score ({avg_first:.1f}/100) "
                f"suggests wrong initial approach.{detail}"
            ),
            depth_at_failure=0,
        )
    return None


def detect_stall(run: dict[str, Any]) -> CauseEvidence | None:
    """Check for consecutive depths with no meaningful improvement."""
    depths = run.get("depths", [])
    if len(depths) < 3:
        return None

    stall_threshold = 2.0  # minimum score movement to count as progress
    max_stall_len = 0
    stall_start = None
    current_stall = 0

    for i in range(1, len(depths)):
        prev_scores = depths[i - 1].get("dimension_scores", {})
        curr_scores = depths[i].get("dimension_scores", {})

        if not prev_scores or not curr_scores:
            current_stall = 0
            continue

        common = set(prev_scores.keys()) & set(curr_scores.keys())
        if not common:
            current_stall = 0
            continue

        avg_prev = sum(prev_scores[d] for d in common) / len(common)
        avg_curr = sum(curr_scores[d] for d in common) / len(common)
        delta = abs(avg_curr - avg_prev)

        if delta < stall_threshold:
            current_stall += 1
            if current_stall > max_stall_len:
                max_stall_len = current_stall
                stall_start = i - current_stall
        else:
            current_stall = 0

    if max_stall_len >= 2:
        confidence = min(1.0, 0.5 + max_stall_len * 0.15)
        return CauseEvidence(
            cause=FailureCause.STALL,
            confidence=round(confidence, 2),
            evidence=(
                f"No meaningful improvement for {max_stall_len} consecutive depths "
                f"starting at depth {stall_start}"
            ),
            depth_at_failure=stall_start + max_stall_len if stall_start is not None else None,
        )
    return None


def detect_regression(run: dict[str, Any]) -> CauseEvidence | None:
    """Check if scores got worse overall from start to end."""
    depths = run.get("depths", [])
    if len(depths) < 2:
        return None

    first_scores = depths[0].get("dimension_scores", {})
    last_scores = depths[-1].get("dimension_scores", {})

    if not first_scores or not last_scores:
        return None

    common = set(first_scores.keys()) & set(last_scores.keys())
    if not common:
        return None

    avg_first = sum(first_scores[d] for d in common) / len(common)
    avg_last = sum(last_scores[d] for d in common) / len(common)

    if avg_last < avg_first:
        drop = avg_first - avg_last
        confidence = min(1.0, 0.4 + drop / 50)
        regressed_dims = [
            d for d in common if last_scores[d] < first_scores[d]
        ]
        return CauseEvidence(
            cause=FailureCause.REGRESSION,
            confidence=round(confidence, 2),
            evidence=(
                f"Overall regression: avg score dropped {avg_first:.1f} -> {avg_last:.1f} "
                f"({drop:.1f} pts). Regressed dimensions: {', '.join(regressed_dims)}"
            ),
            depth_at_failure=len(depths) - 1,
        )
    return None


def detect_timeout(run: dict[str, Any]) -> CauseEvidence | None:
    """Check if run was terminated due to timeout."""
    if run.get("timed_out", False):
        return CauseEvidence(
            cause=FailureCause.TIMEOUT,
            confidence=0.95,
            evidence="Run was terminated due to timeout",
            depth_at_failure=run.get("depth_reached"),
        )
    return None


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------

def build_timeline(run: dict[str, Any]) -> list[str]:
    """Build a chronological event list from run data."""
    timeline: list[str] = []
    depths = run.get("depths", [])

    task = run.get("task", "unknown task")
    task_type = run.get("task_type", "")
    max_depth = run.get("max_depth", "?")
    type_label = f" (type: {task_type})" if task_type else ""
    timeline.append(f"[start] Task: {task}{type_label}, max_depth={max_depth}")

    for d in depths:
        depth_num = d.get("depth", "?")
        scores = d.get("dimension_scores", {})
        strategy = d.get("strategy", "")
        weaknesses = d.get("weaknesses", [])

        if scores:
            avg = sum(scores.values()) / len(scores)
            score_str = f"avg={avg:.1f}"
            dims = ", ".join(f"{k}={v}" for k, v in sorted(scores.items()))
            timeline.append(f"[depth {depth_num}] Scores: {score_str} ({dims})")
        else:
            timeline.append(f"[depth {depth_num}] No scores recorded")

        if strategy:
            timeline.append(f"[depth {depth_num}] Strategy: {strategy}")

        if weaknesses:
            timeline.append(
                f"[depth {depth_num}] Weaknesses: {', '.join(weaknesses)}"
            )

    satisfied = run.get("satisfied", False)
    depth_reached = run.get("depth_reached", "?")
    outcome = "SATISFIED" if satisfied else "UNSATISFIED"
    timeline.append(f"[end] {outcome} at depth {depth_reached}")

    if run.get("timed_out"):
        timeline.append("[end] Terminated by timeout")

    return timeline


_RECOMMENDATION_MAP: dict[FailureCause, list[str]] = {
    FailureCause.CONTEXT_EXHAUSTION: [
        "Increase max_depth or decompose the task into smaller sub-tasks",
        "Use /extend-context to switch to a larger context window",
        "Consider checkpointing progress and resuming in a fresh session",
    ],
    FailureCause.ERROR_CASCADE: [
        "Pin stable dimensions before iterating on weak ones",
        "Use dimension-isolation strategy: fix one dimension at a time",
        "Add regression guards to prevent multi-dimension collapse",
    ],
    FailureCause.DRIFT_ACCUMULATION: [
        "Lock strategy for at least 2 depths before switching",
        "Use converge with a fixed strategy parameter",
        "Review if the criterion is too vague (causes optimizer to wander)",
    ],
    FailureCause.WRONG_STRATEGY: [
        "Consult the cookbook for recommended strategies for this task type",
        "Start with a pre-exploration phase before committing to a strategy",
        "Use /converge with an explicit strategy hint",
    ],
    FailureCause.STALL: [
        "Try a different strategy -- current approach has plateaued",
        "Increase perturbation magnitude to escape local optimum",
        "Decompose the problem: solve sub-components independently",
    ],
    FailureCause.REGRESSION: [
        "Add explicit regression guards for dimensions that were previously strong",
        "Use the best-of-N approach: keep the best result from each depth",
        "Review the criterion -- it may conflict with itself across dimensions",
    ],
    FailureCause.TIMEOUT: [
        "Increase timeout or reduce task scope",
        "Use a faster model for initial exploration, then switch to Opus for refinement",
        "Check for infinite loops in the convergence criterion",
    ],
    FailureCause.UNKNOWN: [
        "Collect more detailed telemetry for next run",
        "Run with verbose logging enabled",
        "Manually inspect the run output for anomalies",
    ],
}


def generate_recommendations(causes: list[CauseEvidence]) -> list[str]:
    """Generate actionable recommendations based on identified causes."""
    if not causes:
        return _RECOMMENDATION_MAP[FailureCause.UNKNOWN].copy()

    recs: list[str] = []
    seen: set[str] = set()

    # Sort by confidence descending so highest-confidence causes contribute first
    for ce in sorted(causes, key=lambda c: c.confidence, reverse=True):
        for rec in _RECOMMENDATION_MAP.get(ce.cause, []):
            if rec not in seen:
                recs.append(rec)
                seen.add(rec)

    return recs


def analyze_run(run_data: dict[str, Any]) -> AutopsyReport:
    """Produce a complete autopsy report for a single run."""
    run_id = run_data.get("run_id", "unknown")
    task = run_data.get("task", "unknown")

    detectors = [
        detect_context_exhaustion,
        detect_error_cascade,
        detect_drift,
        detect_wrong_strategy,
        detect_stall,
        detect_regression,
        detect_timeout,
    ]

    causes: list[CauseEvidence] = []
    for detector in detectors:
        result = detector(run_data)
        if result is not None:
            causes.append(result)

    # Sort by confidence descending
    causes.sort(key=lambda c: c.confidence, reverse=True)

    primary = causes[0].cause if causes else FailureCause.UNKNOWN
    timeline = build_timeline(run_data)
    recommendations = generate_recommendations(causes)

    # Determine preventability: preventable if primary cause has a known remedy
    preventable = primary != FailureCause.UNKNOWN

    return AutopsyReport(
        run_id=run_id,
        task=task,
        causes=causes,
        timeline=timeline,
        recommendations=recommendations,
        primary_cause=primary,
        was_preventable=preventable,
    )


def find_failed_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter to only unsatisfied runs."""
    return [r for r in runs if not r.get("satisfied", False)]


def format_report(report: AutopsyReport) -> str:
    """Format an autopsy report for human-readable output."""
    lines: list[str] = []
    lines.append(f"{'=' * 60}")
    lines.append(f"SESSION AUTOPSY: {report.run_id}")
    lines.append(f"{'=' * 60}")
    lines.append(f"Task: {report.task}")
    lines.append(
        f"Primary cause: {report.primary_cause.value}"
    )
    lines.append(f"Preventable: {'Yes' if report.was_preventable else 'No'}")
    lines.append("")

    lines.append("--- Timeline ---")
    for event in report.timeline:
        lines.append(f"  {event}")
    lines.append("")

    lines.append("--- Causes ---")
    if not report.causes:
        lines.append("  No specific cause identified")
    for ce in report.causes:
        lines.append(
            f"  [{ce.cause.value}] confidence={ce.confidence:.2f}"
            f"{f' depth={ce.depth_at_failure}' if ce.depth_at_failure is not None else ''}"
        )
        lines.append(f"    {ce.evidence}")
    lines.append("")

    lines.append("--- Recommendations ---")
    for i, rec in enumerate(report.recommendations, 1):
        lines.append(f"  {i}. {rec}")

    lines.append(f"{'=' * 60}")
    return "\n".join(lines)


def format_summary(reports: list[AutopsyReport]) -> str:
    """Format a summary of multiple autopsy reports."""
    lines: list[str] = []
    lines.append(f"{'=' * 60}")
    lines.append("SESSION AUTOPSY SUMMARY")
    lines.append(f"{'=' * 60}")
    lines.append(f"Total failed runs analyzed: {len(reports)}")
    lines.append("")

    # Cause frequency
    cause_counts: dict[FailureCause, int] = {}
    preventable_count = 0
    for r in reports:
        cause_counts[r.primary_cause] = cause_counts.get(r.primary_cause, 0) + 1
        if r.was_preventable:
            preventable_count += 1

    lines.append("--- Cause Distribution ---")
    for cause, count in sorted(
        cause_counts.items(), key=lambda x: x[1], reverse=True
    ):
        pct = count / len(reports) * 100 if reports else 0
        lines.append(f"  {cause.value}: {count} ({pct:.0f}%)")

    lines.append("")
    pct_prev = preventable_count / len(reports) * 100 if reports else 0
    lines.append(
        f"Preventable: {preventable_count}/{len(reports)} ({pct_prev:.0f}%)"
    )

    # Top recommendations
    rec_freq: dict[str, int] = {}
    for r in reports:
        for rec in r.recommendations[:3]:  # top 3 per report
            rec_freq[rec] = rec_freq.get(rec, 0) + 1

    if rec_freq:
        lines.append("")
        lines.append("--- Top Recommendations ---")
        sorted_recs = sorted(rec_freq.items(), key=lambda x: x[1], reverse=True)
        for i, (rec, count) in enumerate(sorted_recs[:5], 1):
            lines.append(f"  {i}. ({count}x) {rec}")

    lines.append(f"{'=' * 60}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _find_runs_dir() -> Path:
    """Locate the converge runs data directory."""
    candidates = [
        Path.home() / ".claude" / "data" / "converge-runs",
        Path.home() / ".claude" / "cache" / "converge-runs",
        Path.cwd() / "data" / "converge-runs",
    ]
    for p in candidates:
        if p.is_dir():
            return p
    return candidates[0]  # default even if not present


def _load_run(run_id: str) -> dict[str, Any] | None:
    """Load a single run by ID."""
    runs_dir = _find_runs_dir()
    path = runs_dir / f"{run_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    # Search all JSON files for matching run_id
    if runs_dir.exists():
        for f in runs_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if isinstance(data, dict) and data.get("run_id") == run_id:
                    return data
            except (json.JSONDecodeError, KeyError):
                continue
    return None


def _load_latest_run() -> dict[str, Any] | None:
    """Load the most recently modified run."""
    runs_dir = _find_runs_dir()
    if not runs_dir.exists():
        return None
    files = sorted(runs_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
    if not files:
        return None
    return json.loads(files[-1].read_text())


def _load_all_runs() -> list[dict[str, Any]]:
    """Load all available runs."""
    runs_dir = _find_runs_dir()
    if not runs_dir.exists():
        return []
    runs = []
    for f in sorted(runs_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            if isinstance(data, dict):
                runs.append(data)
        except json.JSONDecodeError:
            continue
    return runs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Session Autopsy -- post-mortem analysis of failed sessions",
    )
    sub = parser.add_subparsers(dest="command")

    autopsy_p = sub.add_parser("autopsy", help="Analyze a specific run")
    autopsy_p.add_argument(
        "run_id", nargs="?", default="latest",
        help="Run ID to analyze, or 'latest'",
    )
    autopsy_p.add_argument(
        "--failed-only", action="store_true",
        help="Only show failed (unsatisfied) runs",
    )

    sub.add_parser("summary", help="Summary stats across all failed runs")

    args = parser.parse_args(argv)

    if args.command == "autopsy":
        if args.failed_only:
            runs = _load_all_runs()
            failed = find_failed_runs(runs)
            if not failed:
                print("No failed runs found.")
                return 0
            for run in failed:
                report = analyze_run(run)
                print(format_report(report))
                print()
            return 0

        if args.run_id == "latest":
            run_data = _load_latest_run()
        else:
            run_data = _load_run(args.run_id)

        if run_data is None:
            print(f"Run not found: {args.run_id}", file=sys.stderr)
            return 1

        report = analyze_run(run_data)
        print(format_report(report))
        return 0

    elif args.command == "summary":
        runs = _load_all_runs()
        failed = find_failed_runs(runs)
        if not failed:
            print("No failed runs found.")
            return 0
        reports = [analyze_run(r) for r in failed]
        print(format_summary(reports))
        return 0

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
