#!/usr/bin/env python3
"""
benchmark_backfill.py -- Historical backfill with temporal windowing.

Partitions GRIP's metric history into time-ordered windows to enable
trend analysis across sessions. Outputs windowed summaries to JSONL.

Three operations:
    A. Facet windowing: partition facets into quintiles, compute per-window metrics
    B. Converge windowing: group converge runs by week
    C. CUBE backfill: replay evo-convergence.jsonl into CUBE as historical observations

All operations are idempotent — re-runs produce identical output.

Usage:
    python3 lib/benchmark_backfill.py all
    python3 lib/benchmark_backfill.py facets
    python3 lib/benchmark_backfill.py converge
    python3 lib/benchmark_backfill.py cube
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
LEARNING_DIR = CLAUDE_DIR / "learning"
FACETS_DIR = CLAUDE_DIR / "usage-data" / "facets"
FACET_WINDOWS_PATH = LEARNING_DIR / "facet-windows.jsonl"
CONVERGE_WINDOWS_PATH = LEARNING_DIR / "converge-windows.jsonl"
EVO_CONVERGENCE_PATH = LEARNING_DIR / "evo-convergence.jsonl"
CONVERGE_METRICS_PATH = LEARNING_DIR / "converge-metrics.jsonl"


# =========================================================================
# Part A: Facet Windowing
# =========================================================================

def backfill_facets(num_windows: int = 5) -> list[dict]:
    """Partition facets into time-ordered windows and compute per-window metrics.

    Sorts facets by filesystem mtime (proxy for session time), divides into
    num_windows quintiles, and computes achievement_rate, friction_density,
    and satisfaction_rate per window.

    Returns list of window summaries. Writes to facet-windows.jsonl.
    """
    if not FACETS_DIR.exists():
        print("[FACETS] SKIP: No facets directory")
        return []

    facet_files = sorted(FACETS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    if not facet_files:
        print("[FACETS] SKIP: No facet files")
        return []

    # Load facets with mtime
    facets = []
    for fp in facet_files:
        try:
            data = json.loads(fp.read_text())
            data["_mtime"] = fp.stat().st_mtime
            facets.append(data)
        except Exception:
            continue

    if not facets:
        print("[FACETS] SKIP: No valid facet files")
        return []

    # Partition into windows
    window_size = max(1, len(facets) // num_windows)
    windows = []
    for i in range(num_windows):
        start = i * window_size
        end = start + window_size if i < num_windows - 1 else len(facets)
        window_facets = facets[start:end]
        if not window_facets:
            continue

        total = len(window_facets)
        fully = sum(1 for f in window_facets if f.get("outcome") == "fully_achieved")
        mostly = sum(1 for f in window_facets if f.get("outcome") == "mostly_achieved")
        partially = sum(1 for f in window_facets if f.get("outcome") == "partially_achieved")

        # Friction density: avg friction count per facet
        friction_counts = []
        for f in window_facets:
            fc = f.get("friction_counts", {})
            friction_counts.append(sum(int(v) for v in fc.values() if isinstance(v, (int, float))))
        avg_friction = sum(friction_counts) / total if total else 0

        # Satisfaction: weighted outcome score
        satisfaction = (fully * 1.0 + mostly * 0.75 + partially * 0.25) / total if total else 0

        window_summary = {
            "window": i + 1,
            "total_windows": num_windows,
            "facet_count": total,
            "start_mtime": window_facets[0]["_mtime"],
            "end_mtime": window_facets[-1]["_mtime"],
            "fully_achieved": fully,
            "mostly_achieved": mostly,
            "partially_achieved": partially,
            "achievement_rate": round((fully + mostly) / total, 4) if total else 0,
            "task_efficiency": round(fully / total, 4) if total else 0,
            "friction_density": round(avg_friction, 3),
            "satisfaction_rate": round(satisfaction, 4),
        }
        windows.append(window_summary)

    # Write output
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    with open(FACET_WINDOWS_PATH, "w") as f:
        for w in windows:
            # Remove internal _mtime field
            clean = {k: v for k, v in w.items()}
            f.write(json.dumps(clean) + "\n")

    print(f"[FACETS] {len(facets)} facets -> {len(windows)} windows written to {FACET_WINDOWS_PATH.name}")
    for w in windows:
        print(f"  Window {w['window']}: {w['facet_count']} facets, "
              f"achieve={w['achievement_rate']:.1%}, "
              f"friction={w['friction_density']:.2f}")

    return windows


# =========================================================================
# Part B: Converge Metrics Windowing
# =========================================================================

def backfill_converge() -> list[dict]:
    """Group converge runs by week and compute per-week summaries.

    Reads converge-metrics.jsonl, groups by ISO week, computes avg score,
    avg time, avg efficiency, and contraction rate per week.

    Returns list of weekly summaries. Writes to converge-windows.jsonl.
    """
    if not CONVERGE_METRICS_PATH.exists():
        print("[CONVERGE] SKIP: No converge-metrics.jsonl")
        return []

    runs = []
    for line in CONVERGE_METRICS_PATH.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            runs.append(json.loads(line))
        except Exception:
            continue

    if not runs:
        print("[CONVERGE] SKIP: No valid converge runs")
        return []

    # Group by ISO week
    weekly: dict[str, list] = defaultdict(list)
    for run in runs:
        ts = run.get("timestamp", run.get("started_at", ""))
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            week_key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        except Exception:
            continue
        weekly[week_key].append(run)

    windows = []
    for week_key in sorted(weekly.keys()):
        week_runs = weekly[week_key]
        n = len(week_runs)

        scores = [r.get("final_score", r.get("score", 0)) for r in week_runs]
        times = [r.get("elapsed_sec", 0) for r in week_runs]
        satisfied = sum(1 for r in week_runs if r.get("satisfied", False))

        # Contraction rates from trajectories
        contraction_rates = []
        for r in week_runs:
            trajectory = r.get("trajectory", [])
            if len(trajectory) >= 2:
                try:
                    from lib.converge_metrics import compute_contraction_rate
                    cr = compute_contraction_rate(trajectory)
                    if cr.get("mean_rate") is not None:
                        contraction_rates.append(cr["mean_rate"])
                except Exception:
                    pass

        window = {
            "week": week_key,
            "run_count": n,
            "avg_score": round(sum(scores) / n, 2) if scores else 0,
            "avg_time": round(sum(times) / n, 2) if times else 0,
            "satisfaction_rate": round(satisfied / n, 4) if n else 0,
            "avg_contraction": round(sum(contraction_rates) / len(contraction_rates), 4) if contraction_rates else None,
        }
        windows.append(window)

    # Write output
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONVERGE_WINDOWS_PATH, "w") as f:
        for w in windows:
            f.write(json.dumps(w) + "\n")

    print(f"[CONVERGE] {len(runs)} runs -> {len(windows)} weekly windows written to {CONVERGE_WINDOWS_PATH.name}")
    return windows


# =========================================================================
# Part C: CUBE Backfill from evo-convergence.jsonl
# =========================================================================

def backfill_cube() -> int:
    """Replay evo-convergence.jsonl entries into CUBE as historical observations.

    Uses session_id=backfill-evo-{hash} for idempotency (skips existing).
    Returns count of new observations recorded.
    """
    if not EVO_CONVERGENCE_PATH.exists():
        print("[CUBE-BACKFILL] SKIP: No evo-convergence.jsonl")
        return 0

    entries = []
    for line in EVO_CONVERGENCE_PATH.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue

    if not entries:
        print("[CUBE-BACKFILL] SKIP: No valid entries")
        return 0

    try:
        from lib.cube_session_wire import _load_cube, _save_cube
        from lib.cube import ConvergenceDepth
    except ImportError as e:
        print(f"[CUBE-BACKFILL] SKIP: Import failed: {e}")
        return 0

    cube = _load_cube()

    # Collect existing session IDs to check for duplicates
    existing_sessions = set()
    for (domain, level), cells in cube._cells.items():
        for cell in cells:
            existing_sessions.add(cell.session_id)

    recorded = 0
    for entry in entries:
        cube_scores = entry.get("cube", {})
        if not cube_scores:
            continue

        # Generate deterministic session ID for idempotency
        entry_hash = hashlib.md5(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:8]
        session_id = f"backfill-evo-{entry_hash}"

        if session_id in existing_sessions:
            continue  # Already backfilled

        for domain, score in cube_scores.items():
            if score >= 0.7:
                cd = ConvergenceDepth.CODIFIED
            elif score >= 0.5:
                cd = ConvergenceDepth.UNDERSTOOD
            else:
                cd = ConvergenceDepth.DETECTED
            cube.record(domain, session_id, confidence=score, convergence_depth=cd)
            recorded += 1

        existing_sessions.add(session_id)

    if recorded > 0:
        _save_cube(cube)

    print(f"[CUBE-BACKFILL] {len(entries)} entries -> {recorded} new CUBE observations")
    return recorded


# =========================================================================
# Main
# =========================================================================

def run_all():
    """Run all three backfill operations."""
    print("=" * 60)
    print("GRIP Historical Backfill")
    print("=" * 60)

    print("\n--- Part A: Facet Windowing ---")
    backfill_facets()

    print("\n--- Part B: Converge Windowing ---")
    backfill_converge()

    print("\n--- Part C: CUBE Backfill ---")
    backfill_cube()

    print("\n" + "=" * 60)
    print("Backfill complete.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "all":
        run_all()
    elif cmd == "facets":
        backfill_facets()
    elif cmd == "converge":
        backfill_converge()
    elif cmd == "cube":
        backfill_cube()
    else:
        print(f"Usage: python3 benchmark_backfill.py [all|facets|converge|cube]")
        sys.exit(1)
