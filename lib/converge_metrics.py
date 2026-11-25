#!/usr/bin/env python3
"""
converge_metrics.py -- Convergence loop metrics recording and compound analysis.

The missing bridge between agent-driven /converge commands and the persistence layer.
Records every converge invocation, tracks score trajectories, and proves whether
convergence compounds over time (speed, quality, efficiency).

Two modes of operation:
1. Agent-driven: record_start() -> record_depth() -> record_end()
2. Code-driven: converge_tracked() wraps converge() with auto-recording

All events flow through metrics_tracker.track_event() for unified JSONL storage.

Usage:
    # Agent-driven (from /converge command)
    from lib.converge_metrics import record_start, record_depth, record_end
    run_id = record_start(task="fix auth", task_type="code")
    record_depth(run_id, depth=0, score=45, weaknesses=["no refresh logic"])
    record_depth(run_id, depth=1, score=78, weaknesses=["no retry"])
    record_end(run_id, final_score=91, satisfied=True, deliverables=["commit:abc123"])

    # Code-driven (wrapping converge kernel)
    from lib.converge_metrics import converge_tracked
    result = converge_tracked(state, criterion, improve, label="pr-review")

    # Analysis
    python3 lib/converge_metrics.py summary
    python3 lib/converge_metrics.py compound
    python3 lib/converge_metrics.py last
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# Import paths
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
CONVERGE_METRICS_PATH = CLAUDE_DIR / "learning" / "converge-metrics.jsonl"

# =========================================================================
# Multi-Dimensional Scoring (fixes 91 attractor — same class as Ralph #197)
# =========================================================================

# Weight tables mirror the /converge SKILL.md rubrics exactly.
# Each task type has 3 dimensions summing to 1.0.
WEIGHT_TABLES: dict[str, dict[str, float]] = {
    "code":        {"correctness": 0.40, "design": 0.30, "simplicity": 0.30},
    "explanation": {"clarity": 0.40, "accuracy": 0.30, "brevity": 0.30},
    "design":      {"completeness": 0.40, "coherence": 0.30, "elegance": 0.30},
    "analysis":    {"evidence": 0.40, "insight": 0.30, "actionability": 0.30},
    "creative":    {"originality": 0.40, "fitness": 0.30, "polish": 0.30},
    "general":     {"quality": 0.40, "completeness": 0.30, "conciseness": 0.30},
}


def _compute_step_rates(trajectory: list[float], target: float) -> list[float]:
    """Compute per-step contraction rates L_n = |gap_after|/|gap_before|."""
    rates = []
    for i in range(1, len(trajectory)):
        gap_before = abs(target - trajectory[i - 1])
        gap_after = abs(target - trajectory[i])
        rates.append(0.0 if gap_before == 0 else gap_after / gap_before)
    return rates


def compute_contraction_rate(trajectory: list[float], target: float = 100.0) -> dict:
    """Compute contraction rate L for a score trajectory.

    L_n = |target - score[n]| / |target - score[n-1]|. Healthy runs have L < 1.
    phi_proximity DEPRECATED (disproven, see converge-fixed-point.md).
    """
    if len(trajectory) < 2:
        return {
            "rates": [],
            "mean_rate": 0.0,
            "is_contracting": False,
            "phi_proximity": 0.0,
            "monotonic": False,
            "steps": 0,
        }

    rates = _compute_step_rates(trajectory, target)
    mean_rate = sum(rates) / len(rates) if rates else 0.0
    return {
        "rates": [round(r, 4) for r in rates],
        "mean_rate": round(mean_rate, 4),
        "is_contracting": mean_rate < 1.0,
        "phi_proximity": round(abs(mean_rate - 0.6180339887), 4),
        "monotonic": all(r < 1.0 for r in rates),
        "steps": len(rates),
    }


def _progressive_weakness_penalty(weakness_count: int) -> float:
    """Progressive weakness penalty: w=1: -2.0, w=2: -4.5, etc. Cap at 15."""
    if weakness_count <= 0:
        return 0.0
    capped = min(weakness_count, 5)
    return min(sum(2.0 + 0.5 * i for i in range(capped)), 15.0)


def _exponential_spread_penalty(dimension_scores: dict) -> float:
    """Exponential spread penalty: stdev=0 -> 4.0, stdev=5 -> 0.54."""
    scores = list(dimension_scores.values())
    if len(scores) <= 1:
        return 0.0
    mean = sum(scores) / len(scores)
    variance = sum((s - mean) ** 2 for s in scores) / len(scores)
    return 4.0 * math.exp(-0.4 * variance ** 0.5)


def compute_composite_score(
    task_type: str,
    dimension_scores: dict[str, float],
    weakness_count: int = 0,
    commit_count: int = 0,
    deliverable_count: int = 0,
) -> float:
    """Compute calibrated composite score. V4 breaks 91-attractor via
    progressive weakness + exponential spread penalties.
    """
    weights = WEIGHT_TABLES.get(task_type, WEIGHT_TABLES["general"])

    # Weighted average over matching dimensions
    weighted_sum = 0.0
    weight_total = 0.0
    for dim, weight in weights.items():
        if dim in dimension_scores:
            weighted_sum += dimension_scores[dim] * weight
            weight_total += weight

    if weight_total == 0:
        return 0.0

    base = weighted_sum / weight_total

    weakness_penalty = _progressive_weakness_penalty(weakness_count)
    spread_penalty = _exponential_spread_penalty(dimension_scores)
    composite = base - weakness_penalty - spread_penalty
    return round(max(0.0, min(100.0, composite)), 1)


def _ensure_dir():
    CONVERGE_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)


def _write_event(event: dict) -> dict:
    """Append a converge event to the dedicated JSONL file."""
    _ensure_dir()
    event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    with open(CONVERGE_METRICS_PATH, "a") as f:
        f.write(json.dumps(event, default=str) + "\n")

    # Dual-write to user-scoped JSONL
    try:
        from lib.user_detection import get_username
        username = get_username()
        if username != "unknown":
            user_dir = CLAUDE_DIR / "learning" / "users" / username
            user_dir.mkdir(parents=True, exist_ok=True)
            user_metrics = user_dir / "converge-metrics.jsonl"
            with open(user_metrics, "a") as uf:
                uf.write(json.dumps(event, default=str) + "\n")
    except ImportError:
        pass

    # Also write to unified metrics via track_event if available
    try:
        from lib.metrics_tracker import track_event
        track_event(event["event"], **{k: v for k, v in event.items() if k != "event"})
    except ImportError:
        pass

    return event


def _load_events() -> list[dict]:
    """Load all converge events from JSONL."""
    if not CONVERGE_METRICS_PATH.exists():
        return []
    events = []
    with open(CONVERGE_METRICS_PATH) as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


# =========================================================================
# Agent-Driven Recording (for /converge command)
# =========================================================================

def record_start(
    task: str,
    task_type: str = "general",
    max_depth: int = 3,
    criterion: str = "",
    genome_id: str = "",
    expressed_genes: Optional[list[str]] = None,
) -> str:
    """Record the start of an agent-driven converge loop.

    Returns a run_id for correlating subsequent depth/end events.
    """
    run_id = str(uuid.uuid4())[:8]
    event = {
        "event": "converge_start",
        "run_id": run_id,
        "task": task,
        "task_type": task_type,
        "max_depth": max_depth,
        "criterion": criterion,
    }
    if genome_id:
        event["genome_id"] = genome_id
        event["expressed_genes"] = expressed_genes or []
    _write_event(event)
    return run_id


def _resolve_task_type(run_id: str) -> str:
    """Look up task_type from the converge_start event for a run."""
    for e in _load_events():
        if e.get("run_id") == run_id and e.get("event") == "converge_start":
            return e.get("task_type", "general")
    return "general"


def record_depth(
    run_id: str,
    depth: int,
    score: float = 0.0,
    weaknesses: Optional[list[str]] = None,
    strategy: str = "",
    deliverables: Optional[list[str]] = None,
    dimension_scores: Optional[dict[str, float]] = None,
    token_cost: int = 0,
) -> dict:
    """Record a depth iteration. dimension_scores is MANDATORY (issue #235)."""
    task_type = _resolve_task_type(run_id)

    if dimension_scores:
        computed_score = compute_composite_score(
            task_type=task_type,
            dimension_scores=dimension_scores,
            weakness_count=len(weaknesses or []),
            commit_count=len([d for d in (deliverables or []) if d.startswith("commit:")]),
            deliverable_count=len(deliverables or []),
        )
        score_source = "composite"
    else:
        logger.warning(
            "[converge] record_depth called without dimension_scores for run %s depth %d. "
            "This causes the 91-attractor. See issue #235.",
            run_id, depth,
        )
        computed_score = score
        score_source = "legacy_deprecated"

    event = {
        "event": "converge_depth",
        "run_id": run_id,
        "depth": depth,
        "score": computed_score,
        "score_source": score_source,
        "dimension_scores": dimension_scores or {},
        "weaknesses": weaknesses or [],
        "strategy": strategy,
        "deliverables": deliverables or [],
    }
    if token_cost:
        event["token_cost"] = token_cost
    return _write_event(event)


def _resolve_start_info(run_id: str) -> tuple[str, str | None]:
    """Get task_type and start_timestamp from converge_start event."""
    for e in _load_events():
        if e.get("run_id") == run_id and e.get("event") == "converge_start":
            return e.get("task_type", "general"), e.get("timestamp")
    return "general", None


def _auto_elapsed(elapsed_minutes: float, start_timestamp: str | None) -> float:
    """Auto-compute elapsed minutes from start timestamp if not provided."""
    if elapsed_minutes > 0 or not start_timestamp:
        return elapsed_minutes
    try:
        start_dt = datetime.fromisoformat(start_timestamp)
        return (datetime.now(timezone.utc) - start_dt).total_seconds() / 60.0
    except (ValueError, TypeError):
        return elapsed_minutes


def _build_end_event(run_id, score, source, dims, satisfied, depth, elapsed,
                      deliverables, commits, vt, genes, token_cost, budget) -> dict:
    """Construct the converge_end event dict."""
    event = {"event": "converge_end", "run_id": run_id, "final_score": score,
             "score_source": source, "dimension_scores": dims or {},
             "satisfied": satisfied, "depth_reached": depth, "elapsed_minutes": elapsed,
             "deliverables": deliverables or [], "commits": commits,
             "velocity_trajectory": vt or [], "invoked_genes": genes or []}
    if token_cost:
        event["total_token_cost"] = token_cost
    if budget:
        event["budget_mode"] = budget
    return event


def _enrich_end_event(event: dict, velocity_trajectory: list | None) -> None:
    """Add contraction rate and optional cube scores to end event."""
    vt = velocity_trajectory or []
    if len(vt) >= 2:
        event["contraction_rate"] = compute_contraction_rate(vt)
    if os.environ.get("GRIP_CUBE_SCORES_ON_END"):
        try:
            from lib.subsystem_bridge import cube_scores_for_outcome
            cube = cube_scores_for_outcome()
            if cube:
                event["cube_scores"] = cube
        except (ImportError, Exception):
            pass


def record_end(
    run_id: str,
    final_score: float = 0.0,
    satisfied: bool = False,
    depth_reached: int = 0,
    elapsed_minutes: float = 0.0,
    deliverables: Optional[list[str]] = None,
    commits: int = 0,
    velocity_trajectory: Optional[list[float]] = None,
    dimension_scores: Optional[dict[str, float]] = None,
    invoked_genes: Optional[list[str]] = None,
    total_token_cost: int = 0,
    budget_mode: str = "",
) -> dict:
    """Record converge loop completion. dimension_scores MANDATORY."""
    task_type, start_timestamp = _resolve_start_info(run_id)
    elapsed_minutes = _auto_elapsed(elapsed_minutes, start_timestamp)

    if dimension_scores:
        computed_final = compute_composite_score(
            task_type=task_type,
            dimension_scores=dimension_scores,
            commit_count=commits,
            deliverable_count=len(deliverables or []),
        )
        score_source = "composite"
    else:
        logger.warning(
            "[converge] record_end called without dimension_scores for run %s. "
            "This causes the 91-attractor. See issue #235.",
            run_id,
        )
        computed_final = final_score
        score_source = "legacy_deprecated"

    event = _build_end_event(
        run_id, computed_final, score_source, dimension_scores, satisfied,
        depth_reached, elapsed_minutes, deliverables, commits,
        velocity_trajectory, invoked_genes, total_token_cost, budget_mode)
    _enrich_end_event(event, velocity_trajectory)
    return _write_event(event)


# =========================================================================
# Code-Driven Recording (wrapping converge kernel)
# =========================================================================

def converge_tracked(
    state: Any,
    criterion: Callable[[Any], bool],
    improve: Callable[[Any], Any],
    max_depth: int = 3,
    _scorer: Optional[Callable[[Any], float]] = None,
    label: str = "",
    genome_id: str = "",
    expressed_genes: Optional[list[str]] = None,
) -> Any:
    """Wrap converge() with automatic metrics recording.

    Returns ConvergenceResult with metrics automatically persisted.
    """
    from lib.converge import converge, ConvergenceResult

    run_id = record_start(
        task=label or "code-driven",
        task_type="code",
        max_depth=max_depth,
        genome_id=genome_id,
        expressed_genes=expressed_genes,
    )

    # Wrap improve to record each depth
    depth_counter = [0]
    def tracked_improve(s):
        depth_counter[0] += 1
        score = _scorer(s) if _scorer else 0.0
        record_depth(run_id, depth=depth_counter[0], score=score)
        return improve(s)

    result = converge(
        state=state,
        criterion=criterion,
        improve=tracked_improve,
        max_depth=max_depth,
        _scorer=_scorer,
        label=label,
    )

    record_end(
        run_id=run_id,
        final_score=_scorer(result.best_state) if _scorer else 0.0,
        satisfied=result.satisfied,
        depth_reached=result.depth_reached,
        elapsed_minutes=result.elapsed_sec / 60.0,
    )

    return result


# =========================================================================
# Compound Analysis
# =========================================================================

def _apply_run_event(run: dict, e: dict) -> None:
    """Apply a single converge event to a run dict."""
    evt = e.get("event", "")
    if evt == "converge_start":
        run.update({"task": e.get("task", ""), "task_type": e.get("task_type", ""),
                     "max_depth": e.get("max_depth", 3), "started_at": e.get("timestamp", "")})
    elif evt == "converge_depth":
        run["depths"].append({k: e.get(k, d) for k, d in [
            ("depth", 0), ("score", 0), ("score_source", "legacy"),
            ("dimension_scores", {}), ("weaknesses", []), ("strategy", ""),
            ("deliverables", []), ("timestamp", "")]})
    elif evt == "converge_end":
        run.update({"final_score": e.get("final_score", 0), "score_source": e.get("score_source", "legacy"),
                     "dimension_scores": e.get("dimension_scores", {}), "satisfied": e.get("satisfied", False),
                     "depth_reached": e.get("depth_reached", 0), "elapsed_minutes": e.get("elapsed_minutes", 0),
                     "commits": e.get("commits", 0), "ended_at": e.get("timestamp", ""),
                     "deliverables": e.get("deliverables", []), "velocity_trajectory": e.get("velocity_trajectory", [])})


def _load_user_events(username: str) -> list[dict]:
    """Load events from a user-scoped JSONL file."""
    path = CLAUDE_DIR / "learning" / "users" / username / "converge-metrics.jsonl"
    if not path.exists():
        return []
    events = []
    with open(path) as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def get_runs(username: str | None = None) -> list[dict]:
    """Reconstruct complete converge runs from events."""
    events = _load_user_events(username) if username else _load_events()
    runs_map: dict[str, dict] = {}

    for e in events:
        rid = e.get("run_id", "")
        if not rid:
            continue

        if rid not in runs_map:
            runs_map[rid] = {
                "run_id": rid,
                "depths": [],
                "deliverables": [],
            }

        _apply_run_event(runs_map[rid], e)

    return sorted(runs_map.values(), key=lambda r: r.get("started_at", ""))


def _half_avg(runs: list, key: str) -> float:
    """Average a numeric field across a list of run dicts."""
    vals = [r.get(key, 0) for r in runs]
    return sum(vals) / len(vals) if vals else 0.0


def _trend_dir(val: float, pos: str = "improving", neg: str = "declining") -> str:
    """Classify a trend value as positive/negative/stable."""
    return pos if val > 0 else neg if val < 0 else "stable"


def _compute_half_trends(completed: list) -> dict:
    """Split completed runs into halves, compute trend metrics."""
    mid = max(len(completed) // 2, 1)
    h1, h2 = completed[:mid], completed[mid:]
    eff = [r.get("final_score", 0) / max(r.get("elapsed_minutes", 0), 0.01) for r in completed]

    a1s, a2s = _half_avg(h1, "final_score"), _half_avg(h2, "final_score")
    a1t, a2t = _half_avg(h1, "elapsed_minutes"), _half_avg(h2, "elapsed_minutes")
    a1e, a2e = sum(eff[:mid]) / mid, sum(eff[mid:]) / max(len(eff[mid:]), 1)
    a1d, a2d = _half_avg(h1, "depth_reached"), _half_avg(h2, "depth_reached")
    qt, st, et, dt = a2s - a1s, a1t - a2t, a2e - a1e, a1d - a2d

    return {
        "quality": {"trend": round(qt, 1), "direction": _trend_dir(qt),
                    "first_half_avg": round(a1s, 1), "second_half_avg": round(a2s, 1),
                    "all_scores": [r.get("final_score", 0) for r in completed]},
        "speed": {"trend_minutes": round(st, 1), "direction": _trend_dir(st, "faster", "slower"),
                  "first_half_avg_min": round(a1t, 1), "second_half_avg_min": round(a2t, 1),
                  "all_times_min": [round(r.get("elapsed_minutes", 0), 1) for r in completed]},
        "efficiency": {"trend": round(et, 2), "direction": _trend_dir(et),
                       "first_half_avg": round(a1e, 2), "second_half_avg": round(a2e, 2), "unit": "score/min"},
        "depth": {"trend": round(dt, 1), "direction": _trend_dir(dt, "fewer", "more"),
                  "first_half_avg": round(a1d, 1), "second_half_avg": round(a2d, 1)},
        "_quality_trend": qt, "_speed_trend": st, "_efficiency_trend": et, "_avg_score_2": a2s,
    }


def _compute_contraction_rates(completed: list) -> list:
    """Compute Banach contraction rates for each run with trajectory data.

    Iterates completed runs, extracts velocity trajectory or depth scores,
    and calls compute_contraction_rate() for runs with 2+ data points.
    Returns list of contraction rate dicts.
    """
    contraction_rates = []
    for r in completed:
        traj = r.get("velocity_trajectory", [])
        if not traj and r.get("depths"):
            traj = [d.get("score", 0) for d in sorted(r["depths"], key=lambda x: x.get("depth", 0))]
        if len(traj) >= 2:
            cr = compute_contraction_rate(traj)
            contraction_rates.append(cr)
    return contraction_rates


def _detect_attractor(scores: list, composite_count: int) -> dict:
    """Detect score clustering (attractor) via variance analysis.

    Returns dict with detection flag, stats, and composite/legacy run counts.
    """
    score_mean = sum(scores) / len(scores)
    score_variance = sum((s - score_mean) ** 2 for s in scores) / len(scores)
    score_stddev = math.sqrt(score_variance)
    attractor_detected = score_stddev < 2.0 and len(scores) >= 3
    legacy_count = len(scores) - composite_count

    return {
        "detected": attractor_detected,
        "score_mean": round(score_mean, 1),
        "score_stddev": round(score_stddev, 1),
        "score_variance": round(score_variance, 1),
        "threshold": 2.0,
        "composite_runs": composite_count,
        "legacy_runs": legacy_count,
        # Raw values for evidence computation
        "_score_mean": score_mean,
        "_score_stddev": score_stddev,
    }


def _contraction_evidence(contraction_rates: list) -> tuple[float, str]:
    """Compute contraction rate evidence points and description."""
    if not contraction_rates:
        return 0.0, ""
    contracting = sum(1 for cr in contraction_rates if cr["is_contracting"])
    pct = contracting / len(contraction_rates)
    avg_l = sum(cr["mean_rate"] for cr in contraction_rates) / len(contraction_rates)
    if pct >= 0.8:
        return 0.5, f"contraction rate L<1 in {contracting}/{len(contraction_rates)} runs (mean L={avg_l:.3f})"
    return 0.0, ""


def _ceiling_evidence(attractor_detected: bool, has_composite_scores: bool, score_stddev: float, score_mean: float) -> tuple:
    """Return (points, item, caveat) for quality-at-ceiling evidence."""
    if attractor_detected and not has_composite_scores:
        return 0.0, "quality at ceiling (ATTRACTOR — no dimensional scoring)", f"score attractor detected: stddev={score_stddev:.1f}, all scores cluster at {score_mean:.0f}"
    if attractor_detected and has_composite_scores:
        return 0.25, "quality at ceiling (attractor detected, composite scoring active)", f"score attractor persists with composite scoring: stddev={score_stddev:.1f}"
    return 0.5, "quality at ceiling (self-scored)", "quality is self-scored — ceiling may reflect scoring bias"


def _secondary_evidence(
    contraction_rates: list, has_external_scores: bool,
    attractor_detected: bool, quality_improving: bool,
    score_stddev: float, score_mean: float,
) -> tuple:
    """Return (pts, items, caveats) for contraction, external-scoring, and attractor signals."""
    pts, items, caveats = 0.0, [], []
    cp, item = _contraction_evidence(contraction_rates)
    pts += cp
    if item:
        items.append(item)
    elif not contraction_rates:
        caveats.append("no trajectory data for contraction rate analysis")
    if has_external_scores:
        pts += 1
        items.append("external scoring validates quality")
    else:
        caveats.append("no external scoring — all quality scores are self-assessed")
    if attractor_detected and quality_improving:
        caveats.append(f"score attractor detected despite improving trend: stddev={score_stddev:.1f}, mean={score_mean:.0f}")
    return pts, items, caveats


def _compute_evidence(
    quality_trend: float,
    quality_at_ceiling: bool,
    speed_improving: bool,
    efficiency_improving: bool,
    attractor_detected: bool,
    has_composite_scores: bool,
    contraction_rates: list,
    n_completed: int,
    score_stddev: float,
    score_mean: float,
) -> tuple:
    """Tally evidence points, items, and caveats. Returns (points, items, caveats)."""
    min_n_for_trend = 6
    min_n_for_proof = 20
    has_external_scores = False  # TODO: support external scoring
    quality_improving = quality_trend > 0

    evidence_points = 0.0
    evidence_items = []
    caveats = []

    if speed_improving:
        evidence_points += 1
        evidence_items.append("speed trending faster")
    if quality_improving:
        evidence_points += 1
        evidence_items.append("quality improving")
    elif quality_at_ceiling:
        pts, item, caveat = _ceiling_evidence(attractor_detected, has_composite_scores, score_stddev, score_mean)
        evidence_points += pts
        if item:
            evidence_items.append(item)
        if caveat:
            caveats.append(caveat)
    if efficiency_improving:
        evidence_points += 1
        evidence_items.append("efficiency improving")
    if n_completed >= min_n_for_trend:
        evidence_points += 1
        evidence_items.append(f"N={n_completed} sufficient for trend")
    else:
        caveats.append(f"N={n_completed} too small (need {min_n_for_trend}+ for trend, {min_n_for_proof}+ for proof)")

    pts2, items2, caveats2 = _secondary_evidence(
        contraction_rates, has_external_scores, attractor_detected, quality_improving,
        score_stddev, score_mean,
    )
    return evidence_points + pts2, evidence_items + items2, caveats + caveats2


def _determine_verdict(evidence_points: float, n_completed: int) -> tuple:
    """Determine compound verdict and legacy boolean from evidence points.

    Confidence levels:
    - PROVEN: 4+ points and N >= 20
    - LIKELY: 2.5+ points and N >= 6
    - PROMISING: 1.5+ points
    - INSUFFICIENT: below thresholds

    Returns (compound_verdict, compound_proven).
    """
    min_n_for_trend = 6
    min_n_for_proof = 20

    if evidence_points >= 4 and n_completed >= min_n_for_proof:
        compound_verdict = "PROVEN"
    elif evidence_points >= 2.5 and n_completed >= min_n_for_trend:
        compound_verdict = "LIKELY"
    elif evidence_points >= 1.5:
        compound_verdict = "PROMISING"
    else:
        compound_verdict = "INSUFFICIENT"

    compound_proven = compound_verdict in ("PROVEN", "LIKELY")
    return compound_verdict, compound_proven


def _build_per_run_summary(completed: list) -> list:
    """Build per-run summary list of dicts for output.

    Each dict contains run_id, task, score, timing, depth, commits,
    satisfaction, and efficiency metrics.
    """
    per_run = []
    for r in completed:
        commits_val = r.get("commits", 0)
        commits_count = len(commits_val) if isinstance(commits_val, list) else (commits_val or 0)
        elapsed = r.get("elapsed_minutes", 0)
        score = r.get("final_score", 0)
        per_run.append({
            "run_id": r.get("run_id"),
            "task": r.get("task", "")[:50],
            "score": score,
            "score_source": r.get("score_source", "legacy"),
            "dimension_scores": r.get("dimension_scores", {}),
            "time_min": round(elapsed, 1),
            "depth": r.get("depth_reached", 0),
            "commits": commits_count,
            "satisfied": r.get("satisfied", False),
            "efficiency": round(score / elapsed, 1) if elapsed > 0 else 0,
        })
    return per_run


def _build_contraction_summary(contraction_rates: list, completed: list) -> dict:
    """Build contraction rate summary dict from per-run contraction data.

    Computes aggregate statistics: mean L, phi proximity, contracting/monotonic
    percentages, and per-run detail.
    """
    n = len(contraction_rates)
    if not n:
        return {
            "runs_analyzed": 0,
            "mean_L": None,
            "phi_inv": 0.6180,
            "phi_proximity": None,
            "all_contracting_pct": 0,
            "monotonic_pct": 0,
            "per_run": [],
        }

    mean_l = round(sum(cr["mean_rate"] for cr in contraction_rates) / n, 4)
    phi_prox = round(sum(cr["phi_proximity"] for cr in contraction_rates) / n, 4)
    contracting_pct = round(sum(1 for cr in contraction_rates if cr["is_contracting"]) / n * 100, 1)
    monotonic_pct = round(sum(1 for cr in contraction_rates if cr["monotonic"]) / n * 100, 1)
    per_run_detail = [
        {"run_id": completed[i].get("run_id"), "rates": cr["rates"], "mean_L": cr["mean_rate"], "monotonic": cr["monotonic"]}
        for i, cr in enumerate(contraction_rates) if i < len(completed)
    ]

    return {
        "runs_analyzed": n,
        "mean_L": mean_l,
        "phi_inv": 0.6180,
        "phi_proximity": phi_prox,
        "all_contracting_pct": contracting_pct,
        "monotonic_pct": monotonic_pct,
        "per_run": per_run_detail,
    }


def _count_total_commits(completed: list) -> int:
    """Sum commits across completed runs (handles list or int values)."""
    total = 0
    for r in completed:
        c = r.get("commits", 0)
        total += len(c) if isinstance(c, list) else (c or 0)
    return total


def _b3_validation_status(completed: list) -> dict:
    """Compute B3 contraction rate validation status."""
    rates = [r.get("contraction_rate", {}).get("mean_rate", 0) for r in completed]
    rates = [r for r in rates if r > 0]
    n = len(rates)
    verdict = "TESTABLE" if n >= 100 else "PROMISING" if n >= 30 else "INSUFFICIENT_DATA"
    return {"n": n, "target_n": 100,
            "mean_L": round(sum(rates) / n, 4) if n else None, "verdict": verdict}


def _build_attractor_dict(attractor: dict) -> dict:
    """Extract attractor fields for output."""
    return {k: attractor[k] for k in (
        "detected", "score_mean", "score_stddev", "score_variance",
        "threshold", "composite_runs", "legacy_runs")}


def _build_compound_result(
    runs, completed, compound_proven, compound_verdict, evidence_points,
    evidence_items, caveats, trends, attractor, satisfied_count,
    contraction_rates, b3_status, times, total_commits, scores,
) -> dict:
    """Assemble compound_analysis result dict."""
    return {
        "runs_total": len(runs),
        "runs_completed": len(completed),
        "compound_proven": compound_proven,
        "compound_verdict": compound_verdict,
        "evidence_points": round(evidence_points, 1),
        "evidence_items": evidence_items,
        "caveats": caveats,
        "quality": trends["quality"],
        "speed": trends["speed"],
        "efficiency": trends["efficiency"],
        "depth": trends["depth"],
        "attractor": _build_attractor_dict(attractor),
        "convergence_rate": {"satisfied": satisfied_count, "total": len(completed),
                             "pct": round(satisfied_count / len(completed) * 100, 1)},
        "contraction": _build_contraction_summary(contraction_rates, completed),
        "b3_status": b3_status,
        "totals": {"minutes": round(sum(times), 1), "commits": total_commits,
                   "avg_score": round(sum(scores) / len(scores), 1)},
        "per_run": _build_per_run_summary(completed),
    }


def compound_analysis() -> dict:
    """Analyse whether convergence compounds over time."""
    runs = get_runs()
    completed = [r for r in runs if "final_score" in r]

    if len(completed) < 2:
        return {
            "runs_total": len(runs),
            "runs_completed": len(completed),
            "compound_proven": False,
            "message": f"Need 2+ completed runs for trend analysis (have {len(completed)})",
        }

    scores = [r.get("final_score", 0) for r in completed]
    times = [r.get("elapsed_minutes", 0) for r in completed]

    total_commits = _count_total_commits(completed)

    # Compute trends from first/second half split
    trends = _compute_half_trends(completed)

    # Contraction rate analysis (Banach fixed-point theorem)
    contraction_rates = _compute_contraction_rates(completed)

    # Attractor detection via score variance
    composite_count = sum(1 for r in completed if r.get("score_source") == "composite")
    attractor = _detect_attractor(scores, composite_count)

    # Evidence tally with caveats
    evidence_points, evidence_items, caveats = _compute_evidence(
        trends["_quality_trend"], trends["_avg_score_2"] >= 90,
        trends["_speed_trend"] > 0, trends["_efficiency_trend"] > 0,
        attractor["detected"], composite_count > 0, contraction_rates,
        len(completed), attractor["_score_stddev"], attractor["_score_mean"])

    # Verdict determination
    compound_verdict, compound_proven = _determine_verdict(evidence_points, len(completed))

    # Convergence rate
    satisfied_count = sum(1 for r in completed if r.get("satisfied"))

    b3_status = _b3_validation_status(completed)

    return _build_compound_result(
        runs, completed, compound_proven, compound_verdict, evidence_points,
        evidence_items, caveats, trends, attractor, satisfied_count,
        contraction_rates, b3_status, times, total_commits, scores,
    )


def format_summary() -> str:
    """Format a human-readable summary of all converge runs."""
    runs = get_runs()
    if not runs:
        return "No converge runs recorded yet."

    lines = [f"Converge Metrics ({len(runs)} runs)"]
    lines.append("")

    for r in runs:
        score = r.get("final_score", "?")
        task = r.get("task", "unknown")[:60]
        elapsed = r.get("elapsed_minutes", 0)
        depth = r.get("depth_reached", "?")
        satisfied = "Y" if r.get("satisfied") else "N"
        raw_commits = r.get("commits", 0)
        commits = len(raw_commits) if isinstance(raw_commits, list) else (raw_commits or 0)

        lines.append(
            f"  [{r['run_id']}] {task}"
        )
        lines.append(
            f"    Score: {score}/100 | Depth: {depth} | "
            f"Time: {elapsed:.1f}m | Commits: {commits} | Satisfied: {satisfied}"
        )

        # Score trajectory
        if r.get("depths"):
            trajectory = [d["score"] for d in r["depths"]]
            lines.append(f"    Trajectory: {' -> '.join(str(s) for s in trajectory)}")

        lines.append("")

    return "\n".join(lines)


def _format_trend_lines(analysis: dict) -> list[str]:
    """Format quality/speed/efficiency/depth trend lines."""
    lines = []
    q = analysis["quality"]
    lines.append(f"  Quality:    {q['direction']} ({q['first_half_avg']:.0f} -> {q['second_half_avg']:.0f}, delta={q['trend']:+.1f})")
    s = analysis["speed"]
    lines.append(f"  Speed:      {s['direction']} ({s['first_half_avg_min']:.1f}m -> {s['second_half_avg_min']:.1f}m, delta={s['trend_minutes']:+.1f}m)")
    e = analysis["efficiency"]
    lines.append(f"  Efficiency: {e['direction']} ({e['first_half_avg']:.1f} -> {e['second_half_avg']:.1f} {e.get('unit', 'score/min')})")
    if "depth" in analysis:
        d = analysis["depth"]
        lines.append(f"  Depth:      {d['direction']} iterations ({d['first_half_avg']:.1f} -> {d['second_half_avg']:.1f})")
    return lines


def _format_contraction_lines(ct: dict) -> list[str]:
    """Format contraction rate section lines."""
    if ct.get("runs_analyzed", 0) == 0:
        return []
    lines = ["", "  CONTRACTION RATE (empirical)",
             f"    Mean L = {ct['mean_L']:.4f}",
             f"    Contracting (L<1): {ct['all_contracting_pct']:.0f}% of runs",
             f"    Monotonic: {ct['monotonic_pct']:.0f}% of runs",
             "    [phi-proximity DISPROVEN as artifact — geometric model R^2=0.998]"]
    for pr in ct.get("per_run", []):
        rates_str = " -> ".join(f"{r:.3f}" for r in pr["rates"])
        mono = "monotonic" if pr["monotonic"] else "non-monotonic"
        lines.append(f"    [{pr['run_id']}] L = [{rates_str}] mean={pr['mean_L']:.3f} ({mono})")
    return lines


def format_compound() -> str:
    """Format compound analysis as human-readable text."""
    analysis = compound_analysis()

    if not analysis.get("compound_proven") and analysis["runs_completed"] < 2:
        return analysis.get("message", "Insufficient data")

    lines = ["Convergence Compound Analysis",
             f"  Runs: {analysis['runs_completed']} completed / {analysis['runs_total']} total", ""]
    lines.extend(_format_trend_lines(analysis))

    lines.append("")
    verdict = analysis.get("compound_verdict", "INSUFFICIENT")
    ep = analysis.get("evidence_points", 0)
    lines.append(f"  Compound: {verdict} ({ep}/5 evidence points)")

    if analysis.get("evidence_items"):
        lines.append(f"  Evidence: {', '.join(analysis['evidence_items'])}")

    if analysis.get("caveats"):
        lines.append("")
        lines.append("  Caveats:")
        for c in analysis["caveats"]:
            lines.append(f"    - {c}")

    # Attractor detection
    a = analysis.get("attractor", {})
    if a.get("detected"):
        lines.append("")
        lines.append(f"  ATTRACTOR DETECTED: scores cluster at {a['score_mean']:.0f} (stddev={a['score_stddev']:.1f})")
        lines.append(f"    Composite scoring: {a['composite_runs']}/{a['composite_runs'] + a['legacy_runs']} runs")
    elif a:
        lines.append("")
        lines.append(f"  Score variance: stddev={a['score_stddev']:.1f} (threshold={a['threshold']})")

    lines.extend(_format_contraction_lines(analysis.get("contraction", {})))
    return "\n".join(lines)


# =========================================================================
# ASCII Visual Rendering (CLI-friendly)
# =========================================================================

def _bar(value: float, total: float, width: int = 30, label: str = "") -> str:
    """Render an ASCII progress bar."""
    pct = value / total if total > 0 else 0
    filled = int(pct * width)
    empty = width - filled
    bar = f"[{'#' * filled}{'.' * empty}]"
    return f"  {bar} {value:.0f}/{total:.0f} {label}"


def _format_dashboard_runs(completed: list, total: int) -> list[str]:
    """Render run history section with visual bars."""
    lines = [f"  RUNS ({len(completed)} completed / {total} total)", "-" * 60]
    for r in completed:
        score = r.get("final_score", 0)
        satisfied = "OK" if r.get("satisfied") else "MAX"
        lines.append(f"  [{r['run_id']}] {r.get('task', '?')[:40]}")
        lines.append(_bar(score, 100, 30,
                     f"score | {r.get('elapsed_minutes',0):.0f}m | D{r.get('depth_reached',0)} | {r.get('commits',0)}c | {satisfied}"))
        if r.get("depths"):
            trajectory = " -> ".join(f"{d['score']:.0f}" for d in r["depths"])
            lines.append(f"  Trajectory: {trajectory}")
        lines.append("")
    return lines


def _format_dashboard_compound(analysis: dict) -> list[str]:
    """Render compound analysis section with trend icons."""
    lines = ["  COMPOUND ANALYSIS", "-" * 60]
    for key, fld in [("quality", "trend"), ("speed", "trend_minutes"), ("efficiency", "trend")]:
        d = analysis[key]
        icon = "+" if d[fld] > 0 else "-" if d[fld] < 0 else "="
        if key == "quality":
            lines.append(f"  Quality:    [{icon}] {d['first_half_avg']:.0f} -> {d['second_half_avg']:.0f} ({d['direction']})")
        elif key == "speed":
            lines.append(f"  Speed:      [{icon}] {d['first_half_avg_min']:.0f}m -> {d['second_half_avg_min']:.0f}m ({d['direction']})")
        else:
            lines.append(f"  Efficiency: [{icon}] {d['first_half_avg']:.1f} -> {d['second_half_avg']:.1f} score/min ({d['direction']})")
    if "depth" in analysis:
        d = analysis["depth"]
        icon = "+" if d["trend"] > 0 else "-" if d["trend"] < 0 else "="
        lines.append(f"  Depth:      [{icon}] {d['first_half_avg']:.1f} -> {d['second_half_avg']:.1f} iterations ({d['direction']})")
    lines.extend(["", f"  Verdict: {analysis.get('compound_verdict', 'INSUFFICIENT')} ({analysis.get('evidence_points', 0)}/5 evidence points)"])
    if analysis.get("evidence_items"):
        lines.append(f"  Evidence: {', '.join(analysis['evidence_items'])}")
    for c in analysis.get("caveats", []):
        lines.append(f"  Caveat: {c}")
    a = analysis.get("attractor", {})
    if a.get("detected"):
        lines.extend(["", f"  *** ATTRACTOR: scores={a['score_mean']:.0f} stddev={a['score_stddev']:.1f} "
                      f"({a['composite_runs']} composite, {a['legacy_runs']} legacy)"])
    return lines


def format_dashboard() -> str:
    """Render a rich ASCII dashboard for CLI."""
    runs = get_runs()
    completed = [r for r in runs if "final_score" in r]
    analysis = compound_analysis() if len(completed) >= 2 else None

    lines = []
    lines.append("=" * 60)
    lines.append("  CONVERGE COMPOUND DASHBOARD")
    lines.append("=" * 60)
    lines.append("")

    lines.extend(_format_dashboard_runs(completed, len(runs)))

    if analysis:
        lines.extend(_format_dashboard_compound(analysis))
    else:
        lines.append("  Need 2+ completed runs for compound analysis")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


# =========================================================================
# HTML Dashboard (opens in browser)
# =========================================================================

def _build_trajectories_html(completed: list) -> str:
    """Build HTML cards showing score trajectory for each completed run."""
    html = ""
    for i, r in enumerate(completed):
        if r.get("depths"):
            traj = [d["score"] for d in r["depths"]]
            task = r.get("task", "?")[:50]
            html += f"""
            <div class="run-card">
              <h3>Run {i+1}: {task}</h3>
              <div class="trajectory">{' &rarr; '.join(f'<span class="score-badge">{int(s)}</span>' for s in traj)}</div>
              <div class="meta">
                Score: {r.get('final_score', 0):.0f}/100 |
                Time: {r.get('elapsed_minutes', 0):.0f}m |
                Depth: {r.get('depth_reached', 0)} |
                Commits: {r.get('commits', 0)} |
                {'Satisfied' if r.get('satisfied') else 'Max depth'}
              </div>
            </div>"""
    return html


def _build_trend_bars_html(completed: list, times: list) -> str:
    """Build CSS-only bar chart comparing speed and efficiency across runs."""
    max_time = max(times) if times else 1
    max_eff = max(
        r.get("final_score", 0) / max(r.get("elapsed_minutes", 1), 0.1)
        for r in completed
    ) if completed else 1
    html = ""
    for i, r in enumerate(completed):
        t = r.get("elapsed_minutes", 0)
        eff = r.get("final_score", 0) / max(t, 0.1)
        time_pct = (t / max_time * 100) if max_time > 0 else 0
        eff_pct = (eff / max_eff * 100) if max_eff > 0 else 0
        html += f"""
        <div class="chart-group">
          <div class="chart-label">Run {i+1}</div>
          <div class="chart-bars">
            <div class="chart-bar speed" style="width:{time_pct:.0f}%"><span>{t:.0f}m</span></div>
            <div class="chart-bar efficiency" style="width:{eff_pct:.0f}%"><span>{eff:.1f} s/m</span></div>
          </div>
        </div>"""
    return html


def _build_cc_offender_rows(offenders: list) -> str:
    """Build HTML rows for the top-10 CC offender list."""
    rows = ""
    for o in offenders[:10]:
        cc = o.get("cc", 0)
        color = "#22c55e" if cc < 15 else "#f59e0b" if cc < 25 else "#ef4444"
        bar_width = min(cc * 2, 100)
        rows += f"""
        <div style="display:flex;align-items:center;gap:12px;margin:6px 0;">
          <span style="width:200px;font-size:13px;color:#e5e5e5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{o.get('function', '?')}</span>
          <span style="width:180px;font-size:11px;color:#737373;overflow:hidden;text-overflow:ellipsis;">{o.get('file', '?')}</span>
          <div style="flex:1;height:8px;background:#262626;border-radius:4px;overflow:hidden;">
            <div style="width:{bar_width}%;height:100%;background:{color};border-radius:4px;"></div>
          </div>
          <span style="font-size:14px;font-weight:600;color:{color};min-width:40px;text-align:right;">CC:{cc}</span>
        </div>"""
    return rows


def _build_cc_heatmap_html() -> str:
    """Build CC heatmap section for the dashboard from cached snapshot."""
    cc_cache = CLAUDE_DIR / "cache" / "cc-snapshot.json"
    if not cc_cache.exists():
        # Try to generate a fresh snapshot
        try:
            from cc_scanner import snapshot as cc_snapshot
            data = cc_snapshot(str(CLAUDE_DIR / "lib"), threshold=10)
            cc_cache.parent.mkdir(parents=True, exist_ok=True)
            cc_cache.write_text(json.dumps(data, indent=2))
        except Exception:
            return '<div class="section-title">CC Heatmap</div><div style="color:#737373;font-size:13px;">No CC data available. Run: python3 lib/cc_scanner.py --snapshot cache/cc-snapshot.json</div>'

    try:
        data = json.loads(cc_cache.read_text())
    except Exception:
        return ""

    offenders = data.get("offenders", [])
    total = data.get("total_offenders", 0)
    timestamp = data.get("timestamp", "unknown")

    if not offenders:
        return f'<div class="section-title">CC Heatmap</div><div style="color:#22c55e;font-size:13px;">No CC offenders above threshold. Last scan: {timestamp[:10]}</div>'

    # Grade distribution
    grades = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
    for o in offenders:
        g = o.get("grade", "C")
        grades[g] = grades.get(g, 0) + 1

    grade_bars = ""
    grade_colors = {"A": "#22c55e", "B": "#3b82f6", "C": "#f59e0b", "D": "#ef4444", "F": "#dc2626"}
    for g in ("A", "B", "C", "D", "F"):
        if grades[g] > 0:
            grade_bars += f'<span style="display:inline-block;padding:4px 12px;background:{grade_colors[g]}22;color:{grade_colors[g]};border:1px solid {grade_colors[g]}44;border-radius:8px;font-size:13px;margin:4px;">{g}: {grades[g]}</span>'

    # Top 10 offenders
    rows = _build_cc_offender_rows(offenders)

    return f"""
  <div class="section-title">CC Heatmap ({total} offenders)</div>
  <div style="margin-bottom:12px;">{grade_bars}</div>
  <div class="metric-card">
    <h3>Top Offenders (last scan: {timestamp[:10]})</h3>
    {rows}
  </div>"""


def _prepare_dashboard_vars(completed, analysis, scores, times, commits):
    """Prepare template variables for HTML dashboard."""
    verdict = analysis.get("compound_verdict", "INSUFFICIENT")
    ep = analysis.get("evidence_points", 0)
    colors = {"PROVEN": "#22c55e", "LIKELY": "#3b82f6", "PROMISING": "#f59e0b", "INSUFFICIENT": "#ef4444"}
    caveats = "".join(f'<div style="font-size:12px;color:#a3a3a3;margin:4px 0;">&#x26A0; {c}</div>'
                      for c in analysis.get("caveats", []))
    d = analysis.get("depth", {})
    return {
        "q": analysis.get("quality", {}), "s": analysis.get("speed", {}),
        "e": analysis.get("efficiency", {}), "d": d,
        "verdict_text": f"{verdict} ({ep}/5 evidence)",
        "verdict_color": colors.get(verdict, "#737373"),
        "caveats_html": caveats,
        "depth_avg": d.get("second_half_avg", 0),
        "depth_dir": d.get("direction", "n/a"),
        "depth_trend_val": d.get("trend", 0),
        "conv_rate": analysis.get("convergence_rate", {}),
        "totals": analysis.get("totals", {}),
        "scores": scores, "times": times, "commits": commits,
        "completed": completed,
    }


def _open_dashboard(path) -> None:
    """Open HTML dashboard in default browser."""
    import platform, subprocess
    opener = "open" if platform.system() == "Darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass


def _dashboard_css(verdict_color: str) -> str:
    """Return the <style> block for the HTML dashboard."""
    return f"""<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'SF Pro', system-ui, sans-serif; background: #0a0a0a; color: #e5e5e5; padding: 24px; }}
  .header {{ text-align: center; margin-bottom: 32px; }}
  .header h1 {{ font-size: 28px; font-weight: 700; letter-spacing: -0.5px; }}
  .header .verdict {{ display: inline-block; margin-top: 12px; padding: 6px 20px; border-radius: 20px; font-size: 14px; font-weight: 600; background: {verdict_color}22; color: {verdict_color}; border: 1px solid {verdict_color}44; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .metric-card {{ background: #171717; border: 1px solid #262626; border-radius: 12px; padding: 20px; }}
  .metric-card h3 {{ font-size: 13px; color: #737373; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
  .metric-card .value {{ font-size: 32px; font-weight: 700; }}
  .metric-card .trend {{ font-size: 14px; margin-top: 4px; }}
  .trend.up {{ color: #22c55e; }}
  .trend.down {{ color: #ef4444; }}
  .trend.stable {{ color: #737373; }}
  .run-card {{ background: #171717; border: 1px solid #262626; border-radius: 12px; padding: 20px; margin-bottom: 12px; }}
  .run-card h3 {{ font-size: 15px; margin-bottom: 12px; }}
  .trajectory {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }}
  .score-badge {{ display: inline-block; padding: 4px 12px; border-radius: 16px; font-size: 14px; font-weight: 600; background: #3b82f622; color: #3b82f6; border: 1px solid #3b82f644; }}
  .meta {{ font-size: 13px; color: #737373; }}
  .bar-container {{ margin: 8px 0; }}
  .bar {{ height: 8px; border-radius: 4px; background: #262626; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.5s; }}
  .section-title {{ font-size: 18px; font-weight: 600; margin: 24px 0 12px; }}
  .footer {{ text-align: center; margin-top: 32px; font-size: 12px; color: #525252; }}
  .chart-group {{ margin-bottom: 12px; }}
  .chart-label {{ font-size: 12px; color: #a3a3a3; margin-bottom: 4px; }}
  .chart-bars {{ display: flex; flex-direction: column; gap: 4px; }}
  .chart-bar {{ height: 24px; border-radius: 4px; display: flex; align-items: center; padding: 0 8px; font-size: 12px; font-weight: 600; min-width: 60px; transition: width 0.5s; }}
  .chart-bar span {{ white-space: nowrap; }}
  .chart-bar.speed {{ background: linear-gradient(90deg, #3b82f6, #60a5fa); color: #fff; }}
  .chart-bar.efficiency {{ background: linear-gradient(90deg, #22c55e, #4ade80); color: #fff; }}
  .chart-legend {{ display: flex; gap: 16px; margin-bottom: 12px; font-size: 13px; }}
  .chart-legend span {{ display: flex; align-items: center; gap: 6px; }}
  .chart-legend .dot {{ width: 10px; height: 10px; border-radius: 50%; }}
  .summary-stat {{ display: inline-block; padding: 4px 12px; background: #171717; border: 1px solid #262626; border-radius: 8px; font-size: 13px; margin: 4px; }}
</style>"""


def _dashboard_metric_cards(q, s, e, scores, times, depth_avg, depth_dir, depth_trend_val, completed, commits) -> str:
    """Render the 4-card metric grid HTML."""
    return f"""<div class="grid">
    <div class="metric-card">
      <h3>Quality</h3>
      <div class="value">{q.get('second_half_avg', scores[-1] if scores else 0):.0f}<span style="font-size:16px;color:#737373">/100</span></div>
      <div class="trend {'up' if q.get('trend', 0) > 0 else 'down' if q.get('trend', 0) < 0 else 'stable'}">{q.get('direction', 'n/a')} ({q.get('trend', 0):+.0f})</div>
    </div>
    <div class="metric-card">
      <h3>Speed</h3>
      <div class="value">{s.get('second_half_avg_min', times[-1] if times else 0):.0f}<span style="font-size:16px;color:#737373">min</span></div>
      <div class="trend {'up' if s.get('trend_minutes', 0) > 0 else 'down' if s.get('trend_minutes', 0) < 0 else 'stable'}">{s.get('direction', 'n/a')} ({s.get('trend_minutes', 0):+.0f}m)</div>
    </div>
    <div class="metric-card">
      <h3>Efficiency</h3>
      <div class="value">{e.get('second_half_avg', 0):.1f}<span style="font-size:16px;color:#737373">score/min</span></div>
      <div class="trend {'up' if e.get('trend', 0) > 0 else 'down' if e.get('trend', 0) < 0 else 'stable'}">{e.get('direction', 'n/a')} ({e.get('trend', 0):+.1f})</div>
    </div>
    <div class="metric-card">
      <h3>Depth</h3>
      <div class="value">{depth_avg:.1f}<span style="font-size:16px;color:#737373">iterations</span></div>
      <div class="trend {'up' if depth_trend_val > 0 else 'stable'}">{depth_dir} ({len(completed)} runs, {sum(commits)} commits)</div>
    </div>
  </div>"""


def _html_dashboard_template(v: dict, trajectories_html: str, trend_bars_html: str, completed: list, commits: list) -> str:
    """Render the full HTML dashboard template string."""
    q, s, e = v["q"], v["s"], v["e"]
    verdict_text, verdict_color = v["verdict_text"], v["verdict_color"]
    caveats_html = v["caveats_html"]
    depth_avg, depth_dir, depth_trend_val = v["depth_avg"], v["depth_dir"], v["depth_trend_val"]
    conv_rate, totals = v["conv_rate"], v["totals"]
    scores, times = v["scores"], v["times"]
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Converge Compound Dashboard</title>
{_dashboard_css(verdict_color)}
</head>
<body>
  <div class="header">
    <h1>Converge Compound Dashboard</h1>
    <div class="verdict">{verdict_text}</div>
    {caveats_html}
  </div>

  {_dashboard_metric_cards(q, s, e, scores, times, depth_avg, depth_dir, depth_trend_val, completed, commits)}

  <div class="section-title">Trend: Speed vs Efficiency</div>
  <div class="chart-legend">
    <span><span class="dot" style="background:#3b82f6"></span> Time (lower = faster)</span>
    <span><span class="dot" style="background:#22c55e"></span> Efficiency (score/min, higher = better)</span>
  </div>
  {trend_bars_html}

  <div style="margin: 20px 0; text-align: center;">
    <span class="summary-stat">Convergence rate: {conv_rate.get('satisfied', 0)}/{conv_rate.get('total', 0)} ({conv_rate.get('pct', 0):.0f}%)</span>
    <span class="summary-stat">Total: {totals.get('minutes', 0):.0f}min across {totals.get('commits', 0)} commits</span>
    <span class="summary-stat">Avg score: {totals.get('avg_score', 0):.0f}/100</span>
  </div>

  <div class="section-title">Run History</div>
  {trajectories_html}

  {_build_cc_heatmap_html()}

  <div class="footer">
    GRIP Converge Metrics | Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
  </div>
</body>
</html>"""


def generate_html_dashboard() -> str:
    """Generate a self-contained HTML dashboard and open in browser."""
    runs = get_runs()
    completed = [r for r in runs if "final_score" in r]
    analysis = compound_analysis() if len(completed) >= 2 else {}
    scores = [r.get("final_score", 0) for r in completed]
    times = [r.get("elapsed_minutes", 0) for r in completed]
    commits = [len(c) if isinstance(c, list) else (c or 0) for c in (r.get("commits", 0) for r in completed)]
    trajectories_html = _build_trajectories_html(completed)
    trend_bars_html = _build_trend_bars_html(completed, times)
    v = _prepare_dashboard_vars(completed, analysis, scores, times, commits)
    html = _html_dashboard_template(v, trajectories_html, trend_bars_html, completed, commits)

    dashboard_path = CLAUDE_DIR / "cache" / "converge-dashboard.html"
    dashboard_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard_path.write_text(html)
    _open_dashboard(dashboard_path)
    return str(dashboard_path)


# =========================================================================
# CLI HANDLERS (independently callable, no sys.argv dependency)
# =========================================================================

def handle_summary() -> None:
    print(format_summary())


def handle_compound() -> None:
    print(format_compound())


def handle_last() -> None:
    runs = get_runs()
    if runs:
        print(json.dumps(runs[-1], indent=2, default=str))
    else:
        print("No runs recorded.")


def handle_runs() -> None:
    runs = get_runs()
    print(json.dumps(runs, indent=2, default=str))


def handle_dashboard() -> None:
    print(format_dashboard())
    path = generate_html_dashboard()
    print(f"\n  HTML dashboard opened: {path}")


def handle_record_retroactive(
    task: str,
    task_type: str,
    score: float,
    depth: int,
    minutes: float,
    commits_count: int,
    satisfied: bool = True,
) -> None:
    run_id = record_start(task=task, task_type=task_type)
    record_end(
        run_id=run_id,
        final_score=score,
        satisfied=satisfied,
        depth_reached=depth,
        elapsed_minutes=minutes,
        commits=commits_count,
    )
    print(f"Recorded retroactive run: {run_id}")


def handle_attractor() -> None:
    analysis = compound_analysis()
    a = analysis.get("attractor", {})
    if a.get("detected"):
        print(f"ATTRACTOR ACTIVE: all scores cluster at {a['score_mean']:.0f} (stddev={a['score_stddev']:.1f})")
        print(f"  Composite runs: {a['composite_runs']}/{a['composite_runs'] + a['legacy_runs']}")
        print(f"  Fix: use dimension_scores in record_depth() to break the attractor")
        sys.exit(1)
    else:
        print(f"No attractor: stddev={a.get('score_stddev', 'N/A')}, mean={a.get('score_mean', 'N/A')}")
        print(f"  Composite runs: {a.get('composite_runs', 0)}/{a.get('composite_runs', 0) + a.get('legacy_runs', 0)}")


def handle_verify() -> None:
    analysis = compound_analysis()
    errors = []

    if analysis["runs_completed"] < 2:
        errors.append(f"Need 2+ completed runs, have {analysis['runs_completed']}")
    if "compound_verdict" not in analysis:
        errors.append("Missing compound_verdict field")
    if "evidence_points" not in analysis:
        errors.append("Missing evidence_points field")
    if "caveats" not in analysis:
        errors.append("Missing caveats field")
    if "convergence_rate" not in analysis:
        errors.append("No convergence rate data")
    if "depth" not in analysis:
        errors.append("No depth tracking")

    e = analysis.get("efficiency", {})
    if e.get("unit") != "score/min":
        errors.append(f"Efficiency unit wrong: {e.get('unit')}")

    for r in analysis.get("per_run", []):
        if "efficiency" not in r:
            errors.append(f"Run {r['run_id']} missing per-run efficiency")
            break

    if errors:
        print(f"VERIFY FAILED ({len(errors)} errors):")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    q = analysis["quality"]
    s = analysis["speed"]
    cr = analysis.get("convergence_rate", {})
    d = analysis.get("depth", {})
    v = analysis["compound_verdict"]
    ep = analysis["evidence_points"]

    print("VERIFY PASSED (structure OK)")
    print(f"  Verdict: {v} ({ep}/5 evidence points)")
    print(f"  Quality: {q['second_half_avg']:.0f}/100 ({q['direction']})")
    print(f"  Speed: {s['first_half_avg_min']:.0f}m -> {s['second_half_avg_min']:.0f}m ({s['direction']})")
    print(f"  Efficiency: {e['first_half_avg']:.1f} -> {e['second_half_avg']:.1f} score/min ({e['direction']})")
    print(f"  Convergence: {cr.get('satisfied', 0)}/{cr.get('total', 0)} ({cr.get('pct', 0):.0f}%)")
    print(f"  Depth: {d.get('first_half_avg', 0):.1f} -> {d.get('second_half_avg', 0):.1f} iterations ({d.get('direction', 'n/a')})")
    if analysis.get("caveats"):
        print(f"  Caveats ({len(analysis['caveats'])}):")
        for c in analysis["caveats"]:
            print(f"    - {c}")


def _all_stalled(last_3: list) -> bool:
    """Return True if all 3 recent runs had velocity <= 0 at final depth."""
    stalled = 0
    for r in last_3:
        traj = r.get("velocity_trajectory", [])
        if len(traj) >= 2:
            if traj[-1] - traj[-2] <= 0:
                stalled += 1
        elif not r.get("satisfied", False):
            stalled += 1
    return stalled >= 3


def suggest_max_depth(task_type: str, history_file: str = None,
                      task_hint: str = "", budget=None) -> int:
    """Suggest optimal max_depth based on historical convergence data."""
    budget_cap = budget.max_converge_depth() if budget else 99

    # Complexity hint: big/massive/complex/epic tasks get room up to 6
    complexity_words = {"big", "massive", "complex", "epic", "large", "ambitious"}
    hint_lower = task_hint.lower()
    if any(word in hint_lower for word in complexity_words):
        return min(6, budget_cap)

    # history_file kept for backward compat but get_runs() uses global path
    # Default depth: 4 for code/analysis (step 1->2 yields +11.7 avg improvement), 3 for others
    base_default = 4 if task_type in ("code", "analysis") else 3
    if not Path(history_file or CONVERGE_METRICS_PATH).exists():
        return min(base_default, budget_cap)

    # Load recent runs of same type via reconstructed runs (correct schema)
    all_runs = get_runs()
    recent_runs = [
        r for r in all_runs
        if r.get("task_type") == task_type and r.get("satisfied") is not None
    ]

    if len(recent_runs) < 3:
        return min(base_default, budget_cap)  # Not enough history

    last_3 = recent_runs[-3:]

    # Fast converger: all 3 converged at depth <= 2
    # Uses depth_reached (the actual key stored by record_end)
    final_depths = [r.get("depth_reached", 3) for r in last_3]
    if all(d <= 2 for d in final_depths):
        return min(3, budget_cap)  # Cap at 3 — they converge fast

    if _all_stalled(last_3):
        return min(5, budget_cap)  # Allow more room

    return min(base_default, budget_cap)  # Default


# =========================================================================
# CLI DISPATCHER
# =========================================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 lib/converge_metrics.py <command>")
        print("Commands: summary, compound, dashboard, last, attractor, verify, record-retroactive, runs, suggest-depth")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "summary":
        handle_summary()
    elif cmd == "compound":
        handle_compound()
    elif cmd == "last":
        handle_last()
    elif cmd == "runs":
        handle_runs()
    elif cmd == "dashboard":
        handle_dashboard()
    elif cmd == "record-retroactive":
        if len(sys.argv) < 8:
            print("Usage: record-retroactive <task> <type> <score> <depth> <minutes> <commits> <satisfied>")
            sys.exit(1)
        handle_record_retroactive(
            task=sys.argv[2],
            task_type=sys.argv[3],
            score=float(sys.argv[4]),
            depth=int(sys.argv[5]),
            minutes=float(sys.argv[6]),
            commits_count=int(sys.argv[7]),
            satisfied=sys.argv[8].lower() in ("true", "1", "yes") if len(sys.argv) > 8 else True,
        )
    elif cmd == "attractor":
        handle_attractor()
    elif cmd == "verify":
        handle_verify()
    elif cmd == "suggest-depth":
        task_type = sys.argv[2] if len(sys.argv) > 2 else "code"
        task_hint = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else ""
        depth = suggest_max_depth(task_type, task_hint=task_hint)
        print(f"Suggested max_depth for '{task_type}': {depth}")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
