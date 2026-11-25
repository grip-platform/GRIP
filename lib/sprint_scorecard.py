#!/usr/bin/env python3
"""Sprint Scorecard -- unified sprint performance summary.

Composes multiple data sources into a single-page ASCII report card
with letter grades per section and an overall weighted grade.

Sections: Delivery, Efficiency, Quality, Velocity.
Grade scale: A (90+), B (80-89), C (70-79), D (60-69), F (<60).

Usage:
    python3 -m lib.sprint_scorecard              # full scorecard
    python3 -m lib.sprint_scorecard --wave 5     # specific wave
    python3 -m lib.sprint_scorecard --compare 4 5  # side-by-side
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ScorecardSection:
    """One graded section of a sprint scorecard."""

    title: str
    metrics: dict[str, Any]
    grade: str  # e.g. "A", "B+", "C-"


@dataclass
class Scorecard:
    """Complete sprint scorecard."""

    sections: list[ScorecardSection] = field(default_factory=list)
    overall_grade: str = "?"
    summary: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Grade helpers
# ---------------------------------------------------------------------------

def grade_from_score(score: float) -> str:
    """Convert a 0-100 numeric score to a letter grade.

    Scale:
        97+ : A+    93-96 : A    90-92 : A-
        87-89: B+   83-86 : B    80-82 : B-
        77-79: C+   73-76 : C    70-72 : C-
        67-69: D+   63-66 : D    60-62 : D-
        <60  : F
    """
    score = max(0.0, min(100.0, float(score)))
    if score >= 97:
        return "A+"
    if score >= 93:
        return "A"
    if score >= 90:
        return "A-"
    if score >= 87:
        return "B+"
    if score >= 83:
        return "B"
    if score >= 80:
        return "B-"
    if score >= 77:
        return "C+"
    if score >= 73:
        return "C"
    if score >= 70:
        return "C-"
    if score >= 67:
        return "D+"
    if score >= 63:
        return "D"
    if score >= 60:
        return "D-"
    return "F"


def _grade_to_numeric(grade: str) -> float:
    """Convert a letter grade back to the midpoint of its numeric range."""
    table = {
        "A+": 98.5, "A": 94.5, "A-": 91.0,
        "B+": 88.0, "B": 84.5, "B-": 81.0,
        "C+": 78.0, "C": 74.5, "C-": 71.0,
        "D+": 68.0, "D": 64.5, "D-": 61.0,
        "F": 30.0,
    }
    return table.get(grade, 0.0)


# ---------------------------------------------------------------------------
# Section graders
# ---------------------------------------------------------------------------

def compute_delivery_grade(
    prs_merged: int,
    tests_written: int,
    loc: int,
) -> ScorecardSection:
    """Grade delivery volume.

    Heuristic (each contributes equally):
        PRs:   16+ = 100,  8-15 = 80,  4-7 = 70,  1-3 = 60,  0 = 30
        Tests: 500+ = 100, 200-499 = 80, 50-199 = 70, 1-49 = 60, 0 = 30
        LOC:   10k+ = 100, 5k-9999 = 80, 1k-4999 = 70, 1-999 = 60, 0 = 30
    """
    def _bucket(value: int, thresholds: list[int]) -> float:
        # thresholds = [high, mid-high, mid-low, low]  (descending)
        if value >= thresholds[0]:
            return 100.0
        if value >= thresholds[1]:
            return 80.0
        if value >= thresholds[2]:
            return 70.0
        if value >= thresholds[3]:
            return 60.0
        return 30.0

    pr_score = _bucket(prs_merged, [16, 8, 4, 1])
    test_score = _bucket(tests_written, [500, 200, 50, 1])
    loc_score = _bucket(loc, [10000, 5000, 1000, 1])
    avg = (pr_score + test_score + loc_score) / 3.0

    return ScorecardSection(
        title="DELIVERY",
        metrics={
            "PRs merged": prs_merged,
            "Tests written": f"{tests_written:,}",
            "LOC added": f"{loc:,}",
        },
        grade=grade_from_score(avg),
    )


def compute_efficiency_grade(
    cost_per_pr: float,
    cost_per_test: float,
) -> ScorecardSection:
    """Grade ROI / efficiency metrics.

    Lower cost = better:
        $/PR:   <$1 = 100, <$2 = 85, <$5 = 70, <$10 = 60, else 40
        $/Test: <$0.01 = 100, <$0.05 = 85, <$0.10 = 70, <$0.50 = 60, else 40
    """
    def _inverse_bucket(value: float, thresholds: list[float]) -> float:
        if value < thresholds[0]:
            return 100.0
        if value < thresholds[1]:
            return 85.0
        if value < thresholds[2]:
            return 70.0
        if value < thresholds[3]:
            return 60.0
        return 40.0

    pr_score = _inverse_bucket(cost_per_pr, [1.0, 2.0, 5.0, 10.0])
    test_score = _inverse_bucket(cost_per_test, [0.01, 0.05, 0.10, 0.50])
    avg = (pr_score + test_score) / 2.0

    return ScorecardSection(
        title="EFFICIENCY",
        metrics={
            "$/PR": f"${cost_per_pr:.2f}",
            "$/Test": f"${cost_per_test:.2f}",
        },
        grade=grade_from_score(avg),
    )


def compute_quality_grade(
    avg_score: float,
    test_ratio: float,
) -> ScorecardSection:
    """Grade quality metrics.

    avg_score: 0-100 review/quality score (used directly).
    test_ratio: tests per PR. 50+ = 100, 30-49 = 85, 10-29 = 70, 1-9 = 60, 0 = 30.
    """
    def _ratio_score(ratio: float) -> float:
        if ratio >= 50:
            return 100.0
        if ratio >= 30:
            return 85.0
        if ratio >= 10:
            return 70.0
        if ratio >= 1:
            return 60.0
        return 30.0

    ratio_sc = _ratio_score(test_ratio)
    avg = (float(avg_score) + ratio_sc) / 2.0

    return ScorecardSection(
        title="QUALITY",
        metrics={
            "Avg score": f"{avg_score:.0f}",
            "Test ratio": f"{test_ratio:.0f}:1",
        },
        grade=grade_from_score(avg),
    )


def compute_velocity_grade(
    prs_per_hour: float,
    tests_per_hour: float,
) -> ScorecardSection:
    """Grade velocity / speed metrics.

    PRs/hour:   4+ = 100, 2-3.9 = 85, 1-1.9 = 70, 0.5-0.9 = 60, else 40
    Tests/hour: 200+ = 100, 100-199 = 85, 50-99 = 70, 10-49 = 60, else 40
    """
    def _speed_bucket(value: float, thresholds: list[float]) -> float:
        if value >= thresholds[0]:
            return 100.0
        if value >= thresholds[1]:
            return 85.0
        if value >= thresholds[2]:
            return 70.0
        if value >= thresholds[3]:
            return 60.0
        return 40.0

    pr_sc = _speed_bucket(prs_per_hour, [4.0, 2.0, 1.0, 0.5])
    test_sc = _speed_bucket(tests_per_hour, [200.0, 100.0, 50.0, 10.0])
    avg = (pr_sc + test_sc) / 2.0

    return ScorecardSection(
        title="VELOCITY",
        metrics={
            "PRs/hour": f"{prs_per_hour:.1f}",
            "Tests/hour": f"{tests_per_hour:.0f}",
        },
        grade=grade_from_score(avg),
    )


# ---------------------------------------------------------------------------
# Overall grade
# ---------------------------------------------------------------------------

def compute_overall_grade(sections: list[ScorecardSection]) -> str:
    """Weighted average of section grades.

    Weights: Delivery 30%, Quality 30%, Efficiency 20%, Velocity 20%.
    Falls back to equal weights for unknown section titles.
    """
    if not sections:
        return "F"

    weights = {
        "DELIVERY": 0.30,
        "QUALITY": 0.30,
        "EFFICIENCY": 0.20,
        "VELOCITY": 0.20,
    }

    total_weight = 0.0
    weighted_sum = 0.0

    for section in sections:
        w = weights.get(section.title, 1.0 / len(sections))
        weighted_sum += _grade_to_numeric(section.grade) * w
        total_weight += w

    if total_weight == 0:
        return "F"

    return grade_from_score(weighted_sum / total_weight)


# ---------------------------------------------------------------------------
# ASCII formatting
# ---------------------------------------------------------------------------

def _pad_line(content: str, width: int) -> str:
    """Pad content to fit inside box borders (accounting for border chars)."""
    inner = width - 4  # 2 for borders + 2 for spacing
    return f"\u2551 {content:<{inner}} \u2551"


def format_scorecard(scorecard: Scorecard, width: int = 60) -> str:
    """Render a scorecard as an ASCII-bordered report card."""
    inner = width - 4
    lines: list[str] = []

    # Top border
    lines.append("\u2554" + "\u2550" * (width - 2) + "\u2557")

    # Title
    title = "GRIP Sprint Scorecard"
    lines.append(_pad_line(title.center(inner), width))

    # Title separator
    lines.append("\u2560" + "\u2550" * (width - 2) + "\u2563")

    for i, section in enumerate(scorecard.sections):
        # Section header with grade
        grade_str = f"[{section.grade}]"
        header = f"{section.title:<{inner - len(grade_str)}}{grade_str}"
        lines.append(_pad_line(header, width))

        # Metrics — two per row
        metric_items = list(section.metrics.items())
        for row_start in range(0, len(metric_items), 2):
            pair = metric_items[row_start:row_start + 2]
            parts = []
            for name, value in pair:
                parts.append(f"  {name + ':':<14}{str(value):>10}")
            row_text = "".join(parts)
            lines.append(_pad_line(row_text, width))

        # Section separator
        if i < len(scorecard.sections) - 1:
            lines.append("\u2560" + "\u2500" * (width - 2) + "\u2563")

    # Overall grade separator
    lines.append("\u2560" + "\u2550" * (width - 2) + "\u2563")

    # Overall grade
    grade_str = f"[{scorecard.overall_grade}]"
    overall_line = f"{'OVERALL GRADE:':<{inner - len(grade_str)}}{grade_str}"
    lines.append(_pad_line(overall_line, width))

    # Bottom border
    lines.append("\u255a" + "\u2550" * (width - 2) + "\u255d")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare_scorecards(card_a: Scorecard, card_b: Scorecard) -> str:
    """Side-by-side comparison of two scorecards.

    Returns a formatted string showing grade deltas per section.
    """
    lines: list[str] = []
    lines.append(f"{'Section':<20} {'Card A':>8} {'Card B':>8} {'Delta':>8}")
    lines.append("-" * 48)

    sections_a = {s.title: s for s in card_a.sections}
    sections_b = {s.title: s for s in card_b.sections}
    all_titles = list(dict.fromkeys(
        [s.title for s in card_a.sections] + [s.title for s in card_b.sections]
    ))

    for title in all_titles:
        grade_a = sections_a[title].grade if title in sections_a else "-"
        grade_b = sections_b[title].grade if title in sections_b else "-"
        num_a = _grade_to_numeric(grade_a)
        num_b = _grade_to_numeric(grade_b)
        delta = num_b - num_a
        delta_str = f"{delta:+.1f}" if grade_a != "-" and grade_b != "-" else "n/a"
        lines.append(f"{title:<20} {grade_a:>8} {grade_b:>8} {delta_str:>8}")

    lines.append("-" * 48)
    overall_a = card_a.overall_grade
    overall_b = card_b.overall_grade
    num_a = _grade_to_numeric(overall_a)
    num_b = _grade_to_numeric(overall_b)
    delta = num_b - num_a
    lines.append(
        f"{'OVERALL':<20} {overall_a:>8} {overall_b:>8} {delta:+.1f}".rstrip()
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Builder (convenience)
# ---------------------------------------------------------------------------

def build_scorecard(
    prs_merged: int = 0,
    tests_written: int = 0,
    loc: int = 0,
    cost_per_pr: float = 0.0,
    cost_per_test: float = 0.0,
    avg_score: float = 0.0,
    test_ratio: float = 0.0,
    prs_per_hour: float = 0.0,
    tests_per_hour: float = 0.0,
    summary: str = "",
) -> Scorecard:
    """Build a complete scorecard from raw metrics."""
    delivery = compute_delivery_grade(prs_merged, tests_written, loc)
    efficiency = compute_efficiency_grade(cost_per_pr, cost_per_test)
    quality = compute_quality_grade(avg_score, test_ratio)
    velocity = compute_velocity_grade(prs_per_hour, tests_per_hour)

    sections = [delivery, efficiency, quality, velocity]
    overall = compute_overall_grade(sections)

    return Scorecard(
        sections=sections,
        overall_grade=overall,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_wave_data(wave: int) -> dict[str, Any] | None:
    """Load wave data from data/waves/wave-<N>.json if it exists."""
    candidates = [
        Path(f"data/waves/wave-{wave}.json"),
        Path(f"~/.claude/data/waves/wave-{wave}.json").expanduser(),
    ]
    for p in candidates:
        if p.exists():
            return json.loads(p.read_text())
    return None


def _scorecard_from_wave_data(data: dict[str, Any]) -> Scorecard:
    """Build a scorecard from wave JSON data."""
    return build_scorecard(
        prs_merged=data.get("prs_merged", 0),
        tests_written=data.get("tests_written", 0),
        loc=data.get("loc", 0),
        cost_per_pr=data.get("cost_per_pr", 0.0),
        cost_per_test=data.get("cost_per_test", 0.0),
        avg_score=data.get("avg_score", 0.0),
        test_ratio=data.get("test_ratio", 0.0),
        prs_per_hour=data.get("prs_per_hour", 0.0),
        tests_per_hour=data.get("tests_per_hour", 0.0),
        summary=data.get("summary", ""),
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="GRIP Sprint Scorecard -- unified sprint performance summary"
    )
    parser.add_argument(
        "--wave", type=int, default=None,
        help="Show scorecard for a specific wave number",
    )
    parser.add_argument(
        "--compare", type=int, nargs=2, metavar=("WAVE_A", "WAVE_B"),
        help="Compare two waves side-by-side",
    )
    parser.add_argument(
        "--width", type=int, default=60,
        help="Report width in characters (default: 60)",
    )

    args = parser.parse_args(argv)

    if args.compare:
        wave_a, wave_b = args.compare
        data_a = _load_wave_data(wave_a)
        data_b = _load_wave_data(wave_b)
        if not data_a:
            print(f"No data found for wave {wave_a}", file=sys.stderr)
            return 1
        if not data_b:
            print(f"No data found for wave {wave_b}", file=sys.stderr)
            return 1
        card_a = _scorecard_from_wave_data(data_a)
        card_b = _scorecard_from_wave_data(data_b)
        print(f"Wave {wave_a} vs Wave {wave_b}")
        print()
        print(compare_scorecards(card_a, card_b))
        return 0

    if args.wave is not None:
        data = _load_wave_data(args.wave)
        if not data:
            print(f"No data found for wave {args.wave}", file=sys.stderr)
            return 1
        card = _scorecard_from_wave_data(data)
    else:
        # Demo scorecard with example data
        card = build_scorecard(
            prs_merged=16,
            tests_written=1333,
            loc=17000,
            cost_per_pr=0.85,
            cost_per_test=0.01,
            avg_score=87,
            test_ratio=83,
            prs_per_hour=4.0,
            tests_per_hour=333,
            summary="Sprint performance summary",
        )

    print(format_scorecard(card, width=args.width))
    return 0


if __name__ == "__main__":
    sys.exit(main())
