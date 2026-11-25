#!/usr/bin/env python3
"""
baseline_benchmark.py -- Historical baseline comparison framework.

Compares GRIP-enhanced sessions against historical baseline (early sessions
before GRIP matured) using temporal partitioning.

Approach: partition facets into first 40% (baseline) and last 60% (GRIP-enhanced).
Compare per-task-type metrics with honest caveats about causation.

Usage:
    python3 lib/baseline_benchmark.py compare   # Run comparison
    python3 lib/baseline_benchmark.py report     # Full report with caveats
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
FACETS_DIR = CLAUDE_DIR / "usage-data" / "facets"

# Partition ratio: first 40% = baseline, last 60% = GRIP-enhanced
BASELINE_RATIO = 0.4

HONEST_CAVEATS = [
    "Cannot prove causation: GRIP vs natural LLM improvement vs task selection bias",
    "Task distributions changed over time (early sessions were different work types)",
    "GRIP infrastructure matured incrementally, not as a single intervention",
    "Facet timestamps use filesystem mtime (proxy, not internal session time)",
    "Early sessions may have had simpler tasks (learning curve effect)",
    "LLM model capabilities improved between baseline and enhanced periods",
    "No controlled experiment: no parallel non-GRIP sessions for comparison",
]


def _load_facets_sorted() -> list[dict]:
    """Load all facets sorted by filesystem mtime (chronological proxy)."""
    if not FACETS_DIR.exists():
        return []

    facet_files = sorted(FACETS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    facets = []
    for fp in facet_files:
        try:
            data = json.loads(fp.read_text())
            data["_mtime"] = fp.stat().st_mtime
            facets.append(data)
        except Exception:
            continue
    return facets


def _partition(facets: list[dict], ratio: float = BASELINE_RATIO) -> tuple[list[dict], list[dict]]:
    """Split facets into baseline and enhanced periods."""
    split_idx = int(len(facets) * ratio)
    return facets[:split_idx], facets[split_idx:]


def _compute_metrics(facets: list[dict]) -> dict:
    """Compute aggregate metrics for a facet set."""
    if not facets:
        return {"count": 0}

    total = len(facets)
    fully = sum(1 for f in facets if f.get("outcome") == "fully_achieved")
    mostly = sum(1 for f in facets if f.get("outcome") == "mostly_achieved")
    partially = sum(1 for f in facets if f.get("outcome") == "partially_achieved")

    # Friction
    friction_totals = []
    for f in facets:
        fc = f.get("friction_counts", {})
        friction_totals.append(sum(int(v) for v in fc.values() if isinstance(v, (int, float))))

    avg_friction = sum(friction_totals) / total if total else 0

    # Per-task-type breakdown
    by_type: dict[str, list] = defaultdict(list)
    for f in facets:
        categories = f.get("goal_categories", {})
        for cat in categories:
            by_type[cat].append(f)

    type_metrics = {}
    for task_type, type_facets in by_type.items():
        t = len(type_facets)
        tf = sum(1 for f in type_facets if f.get("outcome") == "fully_achieved")
        tm = sum(1 for f in type_facets if f.get("outcome") == "mostly_achieved")
        type_metrics[task_type] = {
            "count": t,
            "achievement_rate": round((tf + tm) / t, 4) if t else 0,
            "full_rate": round(tf / t, 4) if t else 0,
        }

    return {
        "count": total,
        "fully_achieved": fully,
        "mostly_achieved": mostly,
        "partially_achieved": partially,
        "achievement_rate": round((fully + mostly) / total, 4),
        "full_rate": round(fully / total, 4),
        "avg_friction": round(avg_friction, 3),
        "satisfaction": round((fully * 1.0 + mostly * 0.75 + partially * 0.25) / total, 4),
        "by_type": type_metrics,
    }


def compare() -> dict:
    """Run baseline vs enhanced comparison."""
    facets = _load_facets_sorted()
    if len(facets) < 10:
        return {"error": f"Insufficient facets ({len(facets)}). Need >= 10 for comparison."}

    baseline, enhanced = _partition(facets)

    baseline_metrics = _compute_metrics(baseline)
    enhanced_metrics = _compute_metrics(enhanced)

    # Compute deltas
    deltas = {}
    for key in ("achievement_rate", "full_rate", "avg_friction", "satisfaction"):
        b_val = baseline_metrics.get(key, 0)
        e_val = enhanced_metrics.get(key, 0)
        if b_val != 0:
            deltas[key] = {
                "baseline": b_val,
                "enhanced": e_val,
                "absolute_delta": round(e_val - b_val, 4),
                "relative_delta": round((e_val - b_val) / abs(b_val), 4) if b_val else 0,
            }

    # Effect size (Cohen's d) on achievement rates
    try:
        from lib.proof_report import cohens_d
        baseline_outcomes = [1.0 if f.get("outcome") in ("fully_achieved", "mostly_achieved") else 0.0
                             for f in baseline]
        enhanced_outcomes = [1.0 if f.get("outcome") in ("fully_achieved", "mostly_achieved") else 0.0
                            for f in enhanced]
        effect_size = cohens_d(baseline_outcomes, enhanced_outcomes)
    except Exception:
        effect_size = None

    return {
        "total_facets": len(facets),
        "baseline_count": baseline_metrics["count"],
        "enhanced_count": enhanced_metrics["count"],
        "baseline": baseline_metrics,
        "enhanced": enhanced_metrics,
        "deltas": deltas,
        "effect_size": effect_size,
        "caveats": HONEST_CAVEATS,
    }


def format_report(result: dict) -> str:
    """Format comparison as a human-readable report."""
    if "error" in result:
        return f"Error: {result['error']}"

    lines = [
        "=" * 64,
        "  GRIP Baseline Benchmark Report",
        "=" * 64,
        "",
        f"  Total facets: {result['total_facets']}",
        f"  Baseline (first {BASELINE_RATIO:.0%}): {result['baseline_count']} sessions",
        f"  Enhanced (last {1-BASELINE_RATIO:.0%}): {result['enhanced_count']} sessions",
        "",
        f"  {'Metric':<22} {'Baseline':>10} {'Enhanced':>10} {'Delta':>10}",
        f"  {'~'*22} {'~'*10} {'~'*10} {'~'*10}",
    ]

    for key, delta in result.get("deltas", {}).items():
        b = delta["baseline"]
        e = delta["enhanced"]
        d = delta["absolute_delta"]
        sign = "+" if d >= 0 else ""
        # For friction, lower is better
        if key == "avg_friction":
            indicator = " (better)" if d < 0 else " (worse)" if d > 0 else ""
        else:
            indicator = " (better)" if d > 0 else " (worse)" if d < 0 else ""

        if isinstance(b, float) and b <= 1.0:
            lines.append(f"  {key:<22} {b:>9.1%} {e:>9.1%} {sign}{d:>8.1%}{indicator}")
        else:
            lines.append(f"  {key:<22} {b:>10.3f} {e:>10.3f} {sign}{d:>9.3f}{indicator}")

    if result.get("effect_size") is not None:
        es = result["effect_size"]
        magnitude = "large" if abs(es) >= 0.8 else "medium" if abs(es) >= 0.5 else "small" if abs(es) >= 0.2 else "negligible"
        lines.append(f"\n  Effect size (Cohen's d): {es:.3f} ({magnitude})")

    lines.append("\n  Caveats (READ CAREFULLY):")
    for caveat in result.get("caveats", []):
        lines.append(f"    - {caveat}")

    lines.append("")
    lines.append("=" * 64)
    return "\n".join(lines)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"

    result = compare()

    if cmd == "compare":
        print(json.dumps(result, indent=2))
    elif cmd == "report":
        print(format_report(result))
    else:
        print("Usage: python3 baseline_benchmark.py [compare|report]")
        sys.exit(1)


if __name__ == "__main__":
    main()
