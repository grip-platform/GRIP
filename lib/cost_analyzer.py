#!/usr/bin/env python3
"""Session cost analyzer -- ROI metrics per feature.

Analyzes session costs against deliverables to compute ROI metrics
including cost per PR, test, LOC, and commit. Supports pattern
efficiency ranking and wave-over-wave comparison.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from typing import Optional


# Token pricing (USD per million tokens)
PRICING = {
    "opus": {"input": 15.0, "output": 75.0},
    "sonnet": {"input": 3.0, "output": 15.0},
}

# Default estimation: ~2000 tokens per message, 60/40 input/output split
TOKENS_PER_MESSAGE = 2000
INPUT_RATIO = 0.6
OUTPUT_RATIO = 0.4


@dataclass
class SessionCost:
    """Cost data for a single session."""

    session_id: str
    total_cost_usd: float
    duration_minutes: float
    model: str = "opus"
    token_count: int = 0


@dataclass
class Deliverable:
    """A unit of work produced in a session."""

    type: str  # pr, test, loc, commit
    count: int
    description: str = ""


@dataclass
class ROIMetrics:
    """Computed ROI metrics for a session or group of sessions."""

    cost_per_pr: float = 0.0
    cost_per_test: float = 0.0
    cost_per_loc: float = 0.0
    cost_per_commit: float = 0.0
    efficiency_score: float = 0.0


@dataclass
class WaveComparison:
    """Metrics for a single sprint wave."""

    wave_id: str
    pr_count: int = 0
    test_count: int = 0
    loc: int = 0
    cost: float = 0.0
    roi_score: float = 0.0


@dataclass
class SessionData:
    """Combined session cost and deliverables."""

    cost: SessionCost
    deliverables: list[Deliverable] = field(default_factory=list)


def estimate_cost(
    message_count: int,
    model: str = "opus",
    tokens_per_message: int = TOKENS_PER_MESSAGE,
) -> float:
    """Estimate session cost from message count when actual costs unavailable.

    Uses per-model pricing with a 60/40 input/output token split.
    """
    if message_count <= 0:
        return 0.0

    model_key = model.lower()
    if model_key not in PRICING:
        model_key = "opus"

    total_tokens = message_count * tokens_per_message
    input_tokens = total_tokens * INPUT_RATIO
    output_tokens = total_tokens * OUTPUT_RATIO

    prices = PRICING[model_key]
    input_cost = (input_tokens / 1_000_000) * prices["input"]
    output_cost = (output_tokens / 1_000_000) * prices["output"]

    return round(input_cost + output_cost, 6)


def calculate_roi(
    costs: list[SessionCost], deliverables: list[Deliverable]
) -> ROIMetrics:
    """Compute ROI metrics from costs and deliverables.

    Aggregates all costs and deliverable counts, then computes
    per-unit costs and an efficiency score.
    """
    total_cost = sum(c.total_cost_usd for c in costs)

    counts: dict[str, int] = {"pr": 0, "test": 0, "loc": 0, "commit": 0}
    for d in deliverables:
        key = d.type.lower()
        if key in counts:
            counts[key] += d.count

    metrics = ROIMetrics(
        cost_per_pr=_safe_divide(total_cost, counts["pr"]),
        cost_per_test=_safe_divide(total_cost, counts["test"]),
        cost_per_loc=_safe_divide(total_cost, counts["loc"]),
        cost_per_commit=_safe_divide(total_cost, counts["commit"]),
    )
    metrics.efficiency_score = _compute_efficiency_score(total_cost, counts)
    return metrics


def cost_per_pr(session_data: SessionData) -> float:
    """Average cost per PR merged in a session."""
    pr_count = _count_type(session_data.deliverables, "pr")
    return _safe_divide(session_data.cost.total_cost_usd, pr_count)


def cost_per_test(session_data: SessionData) -> float:
    """Cost per test written in a session."""
    test_count = _count_type(session_data.deliverables, "test")
    return _safe_divide(session_data.cost.total_cost_usd, test_count)


def cost_per_loc(session_data: SessionData) -> float:
    """Cost per line of code in a session."""
    loc_count = _count_type(session_data.deliverables, "loc")
    return _safe_divide(session_data.cost.total_cost_usd, loc_count)


def find_efficient_patterns(sessions: list[SessionData]) -> list[dict]:
    """Find patterns that deliver the best value (lowest cost per deliverable).

    Returns sessions sorted by efficiency score (highest first).
    """
    scored = []
    for s in sessions:
        total_deliverables = sum(d.count for d in s.deliverables)
        if total_deliverables == 0 or s.cost.total_cost_usd <= 0:
            continue
        score = total_deliverables / s.cost.total_cost_usd
        scored.append(
            {
                "session_id": s.cost.session_id,
                "efficiency_score": round(score, 4),
                "total_cost": s.cost.total_cost_usd,
                "total_deliverables": total_deliverables,
                "model": s.cost.model,
                "duration_minutes": s.cost.duration_minutes,
            }
        )
    scored.sort(key=lambda x: x["efficiency_score"], reverse=True)
    return scored


def find_expensive_patterns(sessions: list[SessionData]) -> list[dict]:
    """Find patterns with worst value (highest cost per deliverable).

    Returns sessions sorted by cost per deliverable (highest first).
    """
    scored = []
    for s in sessions:
        total_deliverables = sum(d.count for d in s.deliverables)
        if total_deliverables == 0:
            cost_per_item = float("inf")
        elif s.cost.total_cost_usd <= 0:
            continue
        else:
            cost_per_item = s.cost.total_cost_usd / total_deliverables
        scored.append(
            {
                "session_id": s.cost.session_id,
                "cost_per_deliverable": round(cost_per_item, 4)
                if not math.isinf(cost_per_item)
                else float("inf"),
                "total_cost": s.cost.total_cost_usd,
                "total_deliverables": total_deliverables,
                "model": s.cost.model,
                "duration_minutes": s.cost.duration_minutes,
            }
        )
    scored.sort(
        key=lambda x: x["cost_per_deliverable"]
        if not math.isinf(x["cost_per_deliverable"])
        else float("inf"),
        reverse=True,
    )
    return scored


def compare_waves(wave_data: list[WaveComparison]) -> list[WaveComparison]:
    """Compare efficiency across sprint waves.

    Computes ROI score for each wave and returns sorted by score (best first).
    """
    for wave in wave_data:
        total_output = wave.pr_count * 10 + wave.test_count + wave.loc * 0.1
        if wave.cost > 0:
            wave.roi_score = round(total_output / wave.cost, 2)
        else:
            wave.roi_score = 0.0
    return sorted(wave_data, key=lambda w: w.roi_score, reverse=True)


def format_report(
    analysis: ROIMetrics,
    cost: Optional[SessionCost] = None,
    deliverables: Optional[list[Deliverable]] = None,
    waves: Optional[list[WaveComparison]] = None,
) -> str:
    """Format an ASCII ROI report."""
    lines = []
    lines.append("Session ROI Report")

    # Use box-drawing character
    lines.append("\u2500" * 18)

    if cost:
        lines.append(
            f"Cost: ${cost.total_cost_usd:.2f} | Duration: {int(cost.duration_minutes)} min"
        )

    if deliverables:
        counts = {"pr": 0, "test": 0, "loc": 0, "commit": 0}
        for d in deliverables:
            key = d.type.lower()
            if key in counts:
                counts[key] += d.count
        lines.append(
            f"PRs: {counts['pr']} | Tests: {counts['test']} | LOC: {counts['loc']:,}"
        )

    lines.append("")
    lines.append("Per-Unit Costs:")
    lines.append(f"  $/PR:   ${analysis.cost_per_pr:.2f}")
    lines.append(f"  $/Test: ${analysis.cost_per_test:.3f}")
    lines.append(f"  $/LOC:  ${analysis.cost_per_loc:.3f}")

    if waves:
        sorted_waves = sorted(waves, key=lambda w: w.roi_score, reverse=True)
        max_score = sorted_waves[0].roi_score if sorted_waves else 1.0
        if max_score <= 0:
            max_score = 1.0

        lines.append("")
        lines.append("Wave Comparison:")
        for i, w in enumerate(sorted_waves):
            pct = int((w.roi_score / max_score) * 100) if max_score > 0 else 0
            filled = pct // 10
            empty = 10 - filled
            bar = "\u2588" * filled + "\u2591" * empty
            label = " (best)" if i == 0 else ""
            lines.append(f"  {w.wave_id}: {bar} {pct}%{label}")

    return "\n".join(lines)


def _safe_divide(numerator: float, denominator: int) -> float:
    """Divide safely, returning 0.0 when denominator is zero."""
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)


def _count_type(deliverables: list[Deliverable], dtype: str) -> int:
    """Count deliverables of a specific type."""
    return sum(d.count for d in deliverables if d.type.lower() == dtype)


def _compute_efficiency_score(total_cost: float, counts: dict[str, int]) -> float:
    """Compute a composite efficiency score (0-100).

    Weighted combination: PRs (40%), tests (30%), LOC (20%), commits (10%).
    Higher is better. Normalized against baseline expectations.
    """
    if total_cost <= 0:
        return 0.0

    # Baseline: what $1 should produce (reasonable targets)
    baselines = {"pr": 0.5, "test": 20, "loc": 200, "commit": 2}
    weights = {"pr": 0.4, "test": 0.3, "loc": 0.2, "commit": 0.1}

    score = 0.0
    for key, weight in weights.items():
        if counts[key] > 0:
            actual_per_dollar = counts[key] / total_cost
            ratio = actual_per_dollar / baselines[key]
            # Cap individual component at 2x baseline (200%)
            capped = min(ratio, 2.0)
            score += capped * weight * 100

    return round(min(score, 100.0), 1)


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="cost_analyzer",
        description="Session cost analyzer -- ROI metrics per feature",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # roi command
    roi_parser = subparsers.add_parser("roi", help="Overall ROI metrics")
    roi_parser.add_argument(
        "--cost", type=float, default=0.0, help="Total session cost in USD"
    )
    roi_parser.add_argument("--prs", type=int, default=0, help="Number of PRs merged")
    roi_parser.add_argument(
        "--tests", type=int, default=0, help="Number of tests written"
    )
    roi_parser.add_argument(
        "--loc", type=int, default=0, help="Lines of code produced"
    )
    roi_parser.add_argument("--commits", type=int, default=0, help="Number of commits")
    roi_parser.add_argument(
        "--duration", type=float, default=0.0, help="Duration in minutes"
    )

    # breakdown command
    breakdown_parser = subparsers.add_parser("breakdown", help="Per-PR cost breakdown")
    breakdown_parser.add_argument(
        "--cost", type=float, required=True, help="Total session cost"
    )
    breakdown_parser.add_argument(
        "--prs", type=int, required=True, help="Number of PRs"
    )

    # efficient command
    subparsers.add_parser("efficient", help="Show most efficient patterns")

    # expensive command
    subparsers.add_parser("expensive", help="Show most expensive patterns")

    # waves command
    subparsers.add_parser("waves", help="Wave-over-wave comparison")

    # estimate command
    est_parser = subparsers.add_parser("estimate", help="Estimate cost from messages")
    est_parser.add_argument(
        "messages", type=int, help="Number of messages in session"
    )
    est_parser.add_argument(
        "--model", default="opus", choices=["opus", "sonnet"], help="Model used"
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "roi":
        costs = [
            SessionCost(
                session_id="cli",
                total_cost_usd=args.cost,
                duration_minutes=args.duration,
            )
        ]
        deliverables = []
        if args.prs:
            deliverables.append(Deliverable(type="pr", count=args.prs))
        if args.tests:
            deliverables.append(Deliverable(type="test", count=args.tests))
        if args.loc:
            deliverables.append(Deliverable(type="loc", count=args.loc))
        if args.commits:
            deliverables.append(Deliverable(type="commit", count=args.commits))

        metrics = calculate_roi(costs, deliverables)
        report = format_report(metrics, costs[0], deliverables)
        print(report)

    elif args.command == "breakdown":
        per_pr = args.cost / args.prs if args.prs > 0 else 0
        print(f"Total cost: ${args.cost:.2f}")
        print(f"PRs merged: {args.prs}")
        print(f"Cost per PR: ${per_pr:.2f}")

    elif args.command == "estimate":
        estimated = estimate_cost(args.messages, args.model)
        print(f"Model: {args.model}")
        print(f"Messages: {args.messages}")
        print(f"Estimated tokens: {args.messages * TOKENS_PER_MESSAGE:,}")
        print(f"Estimated cost: ${estimated:.4f}")

    elif args.command == "efficient":
        print("No session data provided. Use as library for pattern analysis.")

    elif args.command == "expensive":
        print("No session data provided. Use as library for pattern analysis.")

    elif args.command == "waves":
        print("No wave data provided. Use as library for wave comparison.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
