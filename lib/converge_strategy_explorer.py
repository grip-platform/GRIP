#!/usr/bin/env python3
"""
converge_strategy_explorer.py — Explore and query convergence strategies.

Loads strategy history from converge-metrics.jsonl and the strategy store,
builds profiles showing success rates, improvement deltas, and usage patterns.
Recommends strategies for new tasks based on historical performance.

Usage:
    python3 lib/converge_strategy_explorer.py                         # all strategies
    python3 lib/converge_strategy_explorer.py --domain code           # filter by domain
    python3 lib/converge_strategy_explorer.py --min-success 0.8       # high performers
    python3 lib/converge_strategy_explorer.py --recommend code "fix"  # get recommendation
    python3 lib/converge_strategy_explorer.py --json                  # JSON output
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

_ROOT = Path.home() / ".claude"
METRICS_PATH = _ROOT / "learning" / "converge-metrics.jsonl"
STRATEGY_STORE = _ROOT / "cache" / "convergence-strategies.json"


@dataclass
class StrategyProfile:
    """Aggregated profile for a convergence strategy."""

    name: str
    domain: str
    times_used: int
    success_rate: float
    avg_improvement: float
    avg_depth: float
    last_used: str

    def to_dict(self) -> dict:
        return asdict(self)


def load_metrics(path: Path | None = None) -> list[dict]:
    """Load all events from converge-metrics.jsonl."""
    p = path or METRICS_PATH
    if not p.exists():
        return []
    entries = []
    try:
        text = p.read_text()
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def load_strategies(
    metrics_path: Path | None = None,
    store_path: Path | None = None,
) -> list[dict]:
    """Load strategy data from both the metrics JSONL and the strategy store.

    Returns a unified list of events that build_profiles can aggregate.
    The strategy store entries are converted to a compatible format.
    """
    events = load_metrics(metrics_path)

    sp = store_path or STRATEGY_STORE
    if sp.exists():
        try:
            data = json.loads(sp.read_text())
            if isinstance(data, list):
                for s in data:
                    # Convert store entries into a depth-event-like dict
                    # so build_profiles can process them uniformly.
                    # Only add if this strategy name is not already in events.
                    events.append({
                        "_source": "store",
                        "strategy_name": s.get("strategy_name", ""),
                        "domain": s.get("domain", ""),
                        "times_applied": s.get("times_applied", 1),
                        "times_succeeded": s.get("times_succeeded", 1),
                        "improvement": s.get("improvement", 0),
                        "created_at": s.get("created_at", ""),
                    })
        except (json.JSONDecodeError, OSError):
            pass

    return events


def _collect_runs(events: list[dict]) -> dict[str, dict]:
    """Group events by run_id, extracting start/end/depth info."""
    runs: dict[str, dict] = {}
    for ev in events:
        rid = ev.get("run_id")
        if not rid:
            continue
        if rid not in runs:
            runs[rid] = {"start": None, "end": None, "depths": []}
        etype = ev.get("event", "")
        if etype == "converge_start":
            runs[rid]["start"] = ev
        elif etype == "converge_end":
            runs[rid]["end"] = ev
        elif etype == "converge_depth":
            runs[rid]["depths"].append(ev)
    return runs


def build_profiles(events: list[dict]) -> list[StrategyProfile]:
    """Aggregate events into StrategyProfile instances.

    Groups converge_depth events by strategy name, computing success rate
    from the parent run's satisfied flag, improvement from score deltas,
    and average depth from depth fields.
    """
    runs = _collect_runs(events)

    # Map run_id -> (task_type, satisfied, final_score, timestamp)
    run_meta: dict[str, dict] = {}
    for rid, data in runs.items():
        start = data.get("start") or {}
        end = data.get("end") or {}
        run_meta[rid] = {
            "task_type": start.get("task_type", "general"),
            "satisfied": end.get("satisfied", False),
            "final_score": end.get("final_score", 0),
            "timestamp": end.get("timestamp", start.get("timestamp", "")),
        }

    # Aggregate by (strategy_name, task_type)
    # Each depth event contributes one observation.
    agg: dict[tuple[str, str], dict] = {}

    for rid, data in runs.items():
        meta = run_meta.get(rid, {})
        task_type = meta.get("task_type", "general")
        satisfied = meta.get("satisfied", False)
        ts = meta.get("timestamp", "")

        depths = data.get("depths", [])
        for i, dep in enumerate(depths):
            strat = dep.get("strategy", "")
            if not strat:
                continue
            depth_val = dep.get("depth", 0)
            # Compute score improvement for this depth step
            score = dep.get("score", 0)
            prev_score = 0
            if i > 0:
                prev_score = depths[i - 1].get("score", 0)
            elif data.get("start"):
                prev_score = 0  # first depth starts from 0
            improvement = score - prev_score

            key = (strat, task_type)
            if key not in agg:
                agg[key] = {
                    "times_used": 0,
                    "successes": 0,
                    "improvements": [],
                    "depth_values": [],
                    "last_ts": "",
                }
            agg[key]["times_used"] += 1
            if satisfied:
                agg[key]["successes"] += 1
            agg[key]["improvements"].append(improvement)
            agg[key]["depth_values"].append(depth_val)
            if ts > agg[key]["last_ts"]:
                agg[key]["last_ts"] = ts

    # Also incorporate strategy store entries (no run_id)
    for ev in events:
        if ev.get("_source") != "store":
            continue
        name = ev.get("strategy_name", "")
        domain = ev.get("domain", "")
        if not name:
            continue
        key = (name, domain)
        if key not in agg:
            applied = ev.get("times_applied", 1)
            succeeded = ev.get("times_succeeded", 1)
            imp = ev.get("improvement", 0)
            agg[key] = {
                "times_used": applied,
                "successes": succeeded,
                "improvements": [imp] * applied if imp else [],
                "depth_values": [],
                "last_ts": ev.get("created_at", ""),
            }
        # If already present from metrics, skip (metrics data is richer)

    profiles = []
    for (name, domain), data in agg.items():
        used = data["times_used"]
        rate = data["successes"] / used if used else 0.0
        imps = data["improvements"]
        avg_imp = sum(imps) / len(imps) if imps else 0.0
        dvs = data["depth_values"]
        avg_d = sum(dvs) / len(dvs) if dvs else 0.0

        profiles.append(StrategyProfile(
            name=name,
            domain=domain,
            times_used=used,
            success_rate=round(rate, 4),
            avg_improvement=round(avg_imp, 2),
            avg_depth=round(avg_d, 2),
            last_used=data["last_ts"],
        ))

    profiles.sort(key=lambda p: (p.success_rate, p.avg_improvement), reverse=True)
    return profiles


def query_strategies(
    profiles: list[StrategyProfile] | None = None,
    domain: Optional[str] = None,
    min_success: float = 0.5,
    events: list[dict] | None = None,
) -> list[StrategyProfile]:
    """Filter and sort strategy profiles.

    Args:
        profiles: Pre-built profiles (if None, builds from events or disk).
        domain: Filter to this task_type/domain. Case-insensitive substring match.
        min_success: Minimum success rate threshold.
        events: Raw events to build profiles from (if profiles not provided).
    """
    if profiles is None:
        ev = events if events is not None else load_strategies()
        profiles = build_profiles(ev)

    results = []
    for p in profiles:
        if min_success > 0 and p.success_rate < min_success:
            continue
        if domain and domain.lower() not in p.domain.lower():
            continue
        results.append(p)

    results.sort(key=lambda p: (p.success_rate, p.avg_improvement), reverse=True)
    return results


def recommend_strategy(
    task_type: str,
    profiles: list[StrategyProfile] | None = None,
    task_hint: str = "",
    events: list[dict] | None = None,
) -> Optional[StrategyProfile]:
    """Suggest the best strategy for a task type based on historical success.

    Selection criteria:
    1. Filter to matching domain (task_type)
    2. Require success_rate > 0
    3. Sort by success_rate descending, then avg_improvement descending
    4. If task_hint provided, prefer strategies whose name contains the hint
    5. Return top result or None
    """
    candidates = query_strategies(
        profiles=profiles,
        domain=task_type,
        min_success=0.0,
        events=events,
    )
    # Exclude zero success rate
    candidates = [c for c in candidates if c.success_rate > 0]

    if not candidates:
        return None

    if task_hint:
        hint_lower = task_hint.lower()
        hint_matches = [
            c for c in candidates if hint_lower in c.name.lower()
        ]
        if hint_matches:
            return hint_matches[0]

    return candidates[0]


def format_explorer(profiles: list[StrategyProfile]) -> str:
    """Format profiles as a readable table."""
    if not profiles:
        return "No strategies found."

    # Column widths
    name_w = min(max(len(p.name) for p in profiles), 50)
    domain_w = min(max(len(p.domain) for p in profiles), 12)

    header = (
        f"{'Strategy':<{name_w}}  "
        f"{'Domain':<{domain_w}}  "
        f"{'Used':>5}  "
        f"{'Success':>7}  "
        f"{'Avg Imp':>7}  "
        f"{'Avg Dep':>7}"
    )
    separator = "-" * len(header)

    lines = [header, separator]
    for p in profiles:
        name_display = p.name[:name_w]
        domain_display = p.domain[:domain_w]
        lines.append(
            f"{name_display:<{name_w}}  "
            f"{domain_display:<{domain_w}}  "
            f"{p.times_used:>5}  "
            f"{p.success_rate:>6.0%}  "
            f"{p.avg_improvement:>+6.1f}  "
            f"{p.avg_depth:>7.1f}"
        )

    lines.append(separator)
    lines.append(f"Total: {len(profiles)} strategies")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explore convergence strategy history",
    )
    parser.add_argument(
        "--domain",
        help="Filter by task type / domain (substring match)",
    )
    parser.add_argument(
        "--min-success",
        type=float,
        default=0.5,
        help="Minimum success rate (0.0-1.0, default 0.5)",
    )
    parser.add_argument(
        "--recommend",
        nargs="+",
        metavar=("DOMAIN", "HINT"),
        help="Recommend strategy: --recommend <domain> [hint]",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON",
    )
    parser.add_argument(
        "--metrics-path",
        help="Override metrics JSONL path",
    )
    parser.add_argument(
        "--store-path",
        help="Override strategy store path",
    )

    args = parser.parse_args()

    metrics_p = Path(args.metrics_path) if args.metrics_path else None
    store_p = Path(args.store_path) if args.store_path else None

    events = load_strategies(metrics_path=metrics_p, store_path=store_p)
    all_profiles = build_profiles(events)

    if args.recommend:
        domain = args.recommend[0]
        hint = " ".join(args.recommend[1:]) if len(args.recommend) > 1 else ""
        result = recommend_strategy(
            task_type=domain,
            profiles=all_profiles,
            task_hint=hint,
        )
        if result is None:
            print(f"No strategy recommendation for domain '{domain}'.")
            return 1
        if args.json_output:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(f"Recommended: {result.name}")
            print(f"  Domain:      {result.domain}")
            print(f"  Used:        {result.times_used}x")
            print(f"  Success:     {result.success_rate:.0%}")
            print(f"  Avg improve: {result.avg_improvement:+.1f}")
            print(f"  Avg depth:   {result.avg_depth:.1f}")
        return 0

    filtered = query_strategies(
        profiles=all_profiles,
        domain=args.domain,
        min_success=args.min_success,
    )

    if args.json_output:
        print(json.dumps([p.to_dict() for p in filtered], indent=2))
    else:
        print(format_explorer(filtered))

    return 0


if __name__ == "__main__":
    sys.exit(main())
