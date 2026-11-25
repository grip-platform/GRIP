#!/usr/bin/env python3
"""Convergence pattern library -- canonical patterns catalog.

Detects and catalogs recurring convergence patterns from run data.
Patterns are named recurring structures detected from JSONL run data.

CLI: python3 -m lib.pattern_library patterns|detect|frequency|examples
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Pattern(Enum):
    """Canonical convergence pattern types."""

    RAPID_CONVERGENCE = "rapid_convergence"
    SLOW_BURN = "slow_burn"
    REGRESSION_RECOVERY = "regression_recovery"
    DIMENSION_COLLAPSE = "dimension_collapse"
    STRATEGY_PIVOT = "strategy_pivot"
    STALL = "stall"
    PERFECT_START = "perfect_start"
    LATE_BLOOMER = "late_bloomer"

    @property
    def description(self) -> str:
        descriptions = {
            Pattern.RAPID_CONVERGENCE: "Reaches 90+ in depth 0 or 1",
            Pattern.SLOW_BURN: "Needs 4+ depths to satisfy",
            Pattern.REGRESSION_RECOVERY: "Score drops at some depth then recovers higher",
            Pattern.DIMENSION_COLLAPSE: "All dimensions converge within 5 points of each other",
            Pattern.STRATEGY_PIVOT: "Strategy text changes fundamentally mid-run",
            Pattern.STALL: "Score doesn't improve for 2+ consecutive depths",
            Pattern.PERFECT_START: "Depth 0 already scores 85+",
            Pattern.LATE_BLOOMER: "First 2 depths score <60, final >85",
        }
        return descriptions[self]


@dataclass
class PatternMatch:
    """A detected pattern in a run."""

    pattern: Pattern
    run_id: str
    confidence: float  # 0.0 to 1.0
    evidence: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be 0-1, got {self.confidence}")


def _avg_score(depth_entry: dict[str, Any]) -> float:
    """Average of all dimension scores at a given depth."""
    scores = depth_entry.get("dimension_scores", {})
    if not scores:
        return 0.0
    return sum(scores.values()) / len(scores)


def _avg_scores(depths: list[dict[str, Any]]) -> list[float]:
    """List of average scores across all depths."""
    return [_avg_score(d) for d in depths]


def _strategies(depths: list[dict[str, Any]]) -> list[str]:
    """Extract strategy strings from depths."""
    return [d.get("strategy", "") for d in depths]


def _jaccard_similarity(a: str, b: str) -> float:
    """Word-level Jaccard similarity between two strings."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def _detect_rapid_convergence(run: dict[str, Any]) -> PatternMatch | None:
    """Reaches 90+ average score in depth 0 or 1."""
    depths = run.get("depths", [])
    if not depths:
        return None
    avgs = _avg_scores(depths)
    for i, avg in enumerate(avgs[:2]):
        if avg >= 90:
            confidence = min(1.0, (avg - 90) / 10 + 0.7)
            return PatternMatch(
                pattern=Pattern.RAPID_CONVERGENCE,
                run_id=run["run_id"],
                confidence=round(confidence, 2),
                evidence=f"Average score {avg:.1f} at depth {i}",
            )
    return None


def _detect_slow_burn(run: dict[str, Any]) -> PatternMatch | None:
    """Needs 4+ depths to satisfy."""
    depths = run.get("depths", [])
    if len(depths) < 4:
        return None
    avgs = _avg_scores(depths)
    # Check that early depths don't already satisfy (< 90)
    if any(a >= 90 for a in avgs[:3]):
        return None
    if run.get("satisfied") and len(depths) >= 4:
        confidence = min(1.0, 0.6 + (len(depths) - 4) * 0.1)
        return PatternMatch(
            pattern=Pattern.SLOW_BURN,
            run_id=run["run_id"],
            confidence=round(confidence, 2),
            evidence=f"Satisfied after {len(depths)} depths, early scores: {[round(a, 1) for a in avgs[:3]]}",
        )
    return None


def _detect_regression_recovery(run: dict[str, Any]) -> PatternMatch | None:
    """Score drops at some depth then recovers higher than before the drop."""
    depths = run.get("depths", [])
    if len(depths) < 3:
        return None
    avgs = _avg_scores(depths)
    for i in range(1, len(avgs) - 1):
        if avgs[i] < avgs[i - 1]:
            # Found a drop; check if any later depth recovers above pre-drop
            for j in range(i + 1, len(avgs)):
                if avgs[j] > avgs[i - 1]:
                    drop_size = avgs[i - 1] - avgs[i]
                    recovery = avgs[j] - avgs[i - 1]
                    confidence = min(1.0, 0.5 + drop_size / 30 + recovery / 30)
                    return PatternMatch(
                        pattern=Pattern.REGRESSION_RECOVERY,
                        run_id=run["run_id"],
                        confidence=round(confidence, 2),
                        evidence=f"Drop at depth {i} ({avgs[i-1]:.1f}->{avgs[i]:.1f}), recovered to {avgs[j]:.1f} at depth {j}",
                    )
    return None


def _detect_dimension_collapse(run: dict[str, Any]) -> PatternMatch | None:
    """All dimensions converge within 5 points of each other at final depth."""
    depths = run.get("depths", [])
    if not depths:
        return None
    final = depths[-1]
    scores = final.get("dimension_scores", {})
    if len(scores) < 2:
        return None
    values = list(scores.values())
    spread = max(values) - min(values)
    if spread <= 5:
        confidence = min(1.0, 1.0 - spread / 10)
        return PatternMatch(
            pattern=Pattern.DIMENSION_COLLAPSE,
            run_id=run["run_id"],
            confidence=round(confidence, 2),
            evidence=f"Final depth spread: {spread} points ({dict(scores)})",
        )
    return None


def _detect_strategy_pivot(run: dict[str, Any]) -> PatternMatch | None:
    """Strategy text changes fundamentally mid-run (low Jaccard similarity)."""
    depths = run.get("depths", [])
    strats = _strategies(depths)
    if len(strats) < 2:
        return None
    for i in range(1, len(strats)):
        if strats[i] and strats[i - 1]:
            sim = _jaccard_similarity(strats[i - 1], strats[i])
            if sim < 0.3:
                confidence = min(1.0, 0.5 + (0.3 - sim))
                return PatternMatch(
                    pattern=Pattern.STRATEGY_PIVOT,
                    run_id=run["run_id"],
                    confidence=round(confidence, 2),
                    evidence=f"Strategy changed at depth {i}: '{strats[i-1]}' -> '{strats[i]}' (similarity: {sim:.2f})",
                )
    return None


def _detect_stall(run: dict[str, Any]) -> PatternMatch | None:
    """Score doesn't improve for 2+ consecutive depths."""
    depths = run.get("depths", [])
    if len(depths) < 3:
        return None
    avgs = _avg_scores(depths)
    stall_start = None
    stall_length = 0
    for i in range(1, len(avgs)):
        if avgs[i] <= avgs[i - 1]:
            if stall_start is None:
                stall_start = i - 1
            stall_length = i - stall_start
        else:
            if stall_length >= 2:
                break
            stall_start = None
            stall_length = 0
    if stall_length >= 2 and stall_start is not None:
        confidence = min(1.0, 0.5 + stall_length * 0.15)
        return PatternMatch(
            pattern=Pattern.STALL,
            run_id=run["run_id"],
            confidence=round(confidence, 2),
            evidence=f"No improvement for {stall_length} depths starting at depth {stall_start} (scores: {[round(a, 1) for a in avgs[stall_start:stall_start+stall_length+1]]})",
        )
    return None


def _detect_perfect_start(run: dict[str, Any]) -> PatternMatch | None:
    """Depth 0 already scores 85+."""
    depths = run.get("depths", [])
    if not depths:
        return None
    avg = _avg_score(depths[0])
    if avg >= 85:
        confidence = min(1.0, 0.6 + (avg - 85) / 15 * 0.4)
        return PatternMatch(
            pattern=Pattern.PERFECT_START,
            run_id=run["run_id"],
            confidence=round(confidence, 2),
            evidence=f"Depth 0 average: {avg:.1f}",
        )
    return None


def _detect_late_bloomer(run: dict[str, Any]) -> PatternMatch | None:
    """First 2 depths score <60, final >85."""
    depths = run.get("depths", [])
    if len(depths) < 3:
        return None
    avgs = _avg_scores(depths)
    if avgs[0] < 60 and avgs[1] < 60 and avgs[-1] > 85:
        improvement = avgs[-1] - max(avgs[0], avgs[1])
        confidence = min(1.0, 0.5 + improvement / 60)
        return PatternMatch(
            pattern=Pattern.LATE_BLOOMER,
            run_id=run["run_id"],
            confidence=round(confidence, 2),
            evidence=f"Early scores: [{avgs[0]:.1f}, {avgs[1]:.1f}], final: {avgs[-1]:.1f}",
        )
    return None


# All detectors in order
_DETECTORS = [
    _detect_rapid_convergence,
    _detect_slow_burn,
    _detect_regression_recovery,
    _detect_dimension_collapse,
    _detect_strategy_pivot,
    _detect_stall,
    _detect_perfect_start,
    _detect_late_bloomer,
]


def detect_patterns(run: dict[str, Any]) -> list[PatternMatch]:
    """Find all patterns in a single run."""
    matches = []
    for detector in _DETECTORS:
        match = detector(run)
        if match is not None:
            matches.append(match)
    return matches


def detect_all(runs: list[dict[str, Any]]) -> list[PatternMatch]:
    """Find patterns across all runs."""
    matches = []
    for run in runs:
        matches.extend(detect_patterns(run))
    return matches


def pattern_frequency(matches: list[PatternMatch]) -> dict[Pattern, int]:
    """Count occurrences of each pattern."""
    freq: dict[Pattern, int] = {}
    for m in matches:
        freq[m.pattern] = freq.get(m.pattern, 0) + 1
    return freq


def find_examples(
    pattern: Pattern, matches: list[PatternMatch], limit: int = 3
) -> list[PatternMatch]:
    """Find example runs for a given pattern, sorted by confidence descending."""
    filtered = [m for m in matches if m.pattern == pattern]
    filtered.sort(key=lambda m: m.confidence, reverse=True)
    return filtered[:limit]


def format_pattern_report(matches: list[PatternMatch]) -> str:
    """Formatted report of all detected patterns."""
    if not matches:
        return "No patterns detected."
    lines = ["Convergence Pattern Report", "=" * 40, ""]
    by_pattern: dict[Pattern, list[PatternMatch]] = {}
    for m in matches:
        by_pattern.setdefault(m.pattern, []).append(m)
    for pattern in Pattern:
        group = by_pattern.get(pattern, [])
        if not group:
            continue
        lines.append(f"## {pattern.value} ({len(group)} match{'es' if len(group) != 1 else ''})")
        lines.append(f"   {pattern.description}")
        lines.append("")
        for m in sorted(group, key=lambda x: x.confidence, reverse=True):
            lines.append(f"   [{m.confidence:.0%}] run={m.run_id}: {m.evidence}")
        lines.append("")
    return "\n".join(lines)


def format_frequency_chart(freq: dict[Pattern, int]) -> str:
    """ASCII bar chart of pattern frequencies."""
    if not freq:
        return "No patterns to chart."
    max_count = max(freq.values())
    bar_width = 30
    lines = ["Pattern Frequency", "=" * 40, ""]
    for pattern in Pattern:
        count = freq.get(pattern, 0)
        if count == 0:
            continue
        bar_len = round(count / max_count * bar_width) if max_count > 0 else 0
        bar = "#" * bar_len
        label = pattern.value
        lines.append(f"  {label:<25} |{bar} ({count})")
    lines.append("")
    return "\n".join(lines)


def load_runs(path: str) -> list[dict[str, Any]]:
    """Load runs from a JSONL file."""
    runs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                runs.append(json.loads(line))
    return runs


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Convergence pattern library",
        prog="pattern_library",
    )
    parser.add_argument(
        "command",
        choices=["patterns", "detect", "frequency", "examples"],
        help="Command to run",
    )
    parser.add_argument("args", nargs="*", help="Command arguments")
    parser.add_argument(
        "-f", "--file", default="runs.jsonl", help="JSONL file with run data"
    )

    args = parser.parse_args(argv)
    runs = load_runs(args.file)

    if args.command == "patterns":
        matches = detect_all(runs)
        print(format_pattern_report(matches))

    elif args.command == "detect":
        if not args.args:
            parser.error("detect requires a run_id argument")
        target_id = args.args[0]
        target = [r for r in runs if r["run_id"] == target_id]
        if not target:
            print(f"Run '{target_id}' not found.")
            sys.exit(1)
        matches = detect_patterns(target[0])
        if matches:
            for m in matches:
                print(f"  [{m.confidence:.0%}] {m.pattern.value}: {m.evidence}")
        else:
            print("No patterns detected.")

    elif args.command == "frequency":
        matches = detect_all(runs)
        freq = pattern_frequency(matches)
        print(format_frequency_chart(freq))

    elif args.command == "examples":
        if not args.args:
            parser.error("examples requires a pattern_name argument")
        pattern_name = args.args[0]
        try:
            pattern = Pattern(pattern_name)
        except ValueError:
            print(f"Unknown pattern: {pattern_name}")
            print(f"Valid patterns: {', '.join(p.value for p in Pattern)}")
            sys.exit(1)
        matches = detect_all(runs)
        examples = find_examples(pattern, matches)
        if examples:
            for m in examples:
                print(f"  [{m.confidence:.0%}] run={m.run_id}: {m.evidence}")
        else:
            print(f"No examples found for {pattern_name}.")


if __name__ == "__main__":
    main()
