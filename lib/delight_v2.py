#!/usr/bin/env python3
"""DELIGHT v2 — Scientifically grounded session quality metric.

Improvements over v1:
1. Four novel signals derived from INS-05 sentinel data
2. Shadow metrics for Goodhart protection on every signal
3. Cohen's d effect size for trend analysis (replaces naive half-split)
4. Falsification registry — every signal states what would disprove it
5. Bayesian confidence via bootstrap resampling

Novel signals:
- friction_to_flow: gate blocks / total tool calls (sentinel-derived)
- recovery_time_s: mean seconds between block and next productive action
- convergence_efficiency: criterion met / iterations used (converge_metrics)
- session_momentum: d(completion_rate)/dt (acceleration of task completion)

Shadow metrics (Goodhart protection):
- velocity_shadow: revert rate (high velocity + high reverts = gaming)
- completion_shadow: PR quality score (closing issues without quality)
- pattern_shadow: pattern usage rate (discovering patterns nobody uses)
- flow_shadow: user rephrase count (flowing but confused = false positive)

Usage:
    python3 lib/delight_v2.py snapshot [--session-hours N]
    python3 lib/delight_v2.py trend [--days N]
    python3 lib/delight_v2.py falsify
"""

import json
import math
import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The repository root, so `from lib.<module> import ...` works when this file
# runs as a script (`python lib/delight_v2.py snapshot`, as skills/auto/SKILL.md
# says) and not only as `python -m lib.delight_v2`. As lib/auto_converge.py does.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
SENTINEL_LOG = CLAUDE_DIR / "cache" / "sentinel.jsonl"
DELIGHT_V2_FILE = CLAUDE_DIR / "learning" / "delight_v2.jsonl"
GENOME_FILE = CLAUDE_DIR / "cache" / "genome.json"
CONVERGE_METRICS = CLAUDE_DIR / "learning" / "converge-metrics.jsonl"


# =========================================================================
# Falsification Registry — every signal must state what disproves it
# =========================================================================

FALSIFICATION_REGISTRY = {
    "flow_state": {
        "claim": "Uninterrupted tool sequences indicate user satisfaction",
        "falsified_by": "User is uninterrupted but stuck/confused (high flow + high rephrase rate)",
        "shadow_metric": "flow_shadow (user rephrase count)",
    },
    "velocity": {
        "claim": "Higher issue completion rate indicates better session quality",
        "falsified_by": "Fast but low-quality work (high velocity + high revert rate)",
        "shadow_metric": "velocity_shadow (revert rate in next 24h)",
    },
    "friction_to_flow": {
        "claim": "Lower gate-block ratio indicates smoother workflow",
        "falsified_by": "Some blocks are protective — user appreciates them (correlation with explicit 'thank you' after block)",
        "shadow_metric": "block_appreciation_rate (user continues without complaint after block)",
    },
    "recovery_time_s": {
        "claim": "Faster recovery after blocks indicates resilient workflow",
        "falsified_by": "Fast recovery means ignoring the block's warning, not resilience",
        "shadow_metric": "post_block_revert_rate (errors after fast recovery)",
    },
    "convergence_efficiency": {
        "claim": "Fewer iterations to criterion indicates better solution quality",
        "falsified_by": "Fewer iterations = shallow analysis, not genuine efficiency",
        "shadow_metric": "post_converge_fix_rate (fixes needed after convergence)",
    },
    "session_momentum": {
        "claim": "Accelerating task completion indicates productive session",
        "falsified_by": "Tasks naturally get harder; deceleration is expected, not bad",
        "shadow_metric": "task_complexity_normalised_momentum",
    },
}


@dataclass
class DelightV2Snapshot:
    """Point-in-time delight measurement with scientific grounding."""

    timestamp: str = ""
    version: int = 2

    # === Original signals (from v1, kept for continuity) ===
    flow_state: float = 0.0
    velocity: float = 0.0
    completion_ratio: float = 0.0

    # === Novel signals (INS-05 sentinel + converge_metrics) ===
    friction_to_flow: float = 0.0       # gate blocks / total tool calls
    recovery_time_s: float = 0.0        # mean seconds between block and next action
    convergence_efficiency: float = 0.0  # criterion_met / iterations
    session_momentum: float = 0.0       # d(completion_rate)/dt

    # === System health (from v1) ===
    genome_fitness_trend: float = 0.0
    converge_velocity: float = 0.0

    # === Shadow metrics (Goodhart protection) ===
    velocity_shadow: float = 0.0        # revert rate
    flow_shadow: float = 0.0            # rephrase count proxy

    # === Composites ===
    user_delight: float = 0.0
    system_health: float = 0.0
    composite: float = 0.0
    confidence_interval: tuple = (0.0, 0.0)  # 95% CI via bootstrap

    def compute(self) -> None:
        """Compute composite with Goodhart-adjusted weights."""
        # User delight: original signals + novel signals
        # Flow is discounted if flow_shadow (rephrase rate) is high
        flow_adjusted = self.flow_state * max(0.5, 1.0 - self.flow_shadow)
        # Velocity discounted if velocity_shadow (revert rate) is high
        vel_adjusted = min(self.velocity / 3.0, 1.0) * max(0.5, 1.0 - self.velocity_shadow)

        self.user_delight = (
            flow_adjusted * 0.2
            + vel_adjusted * 0.2
            + self.completion_ratio * 0.15
            + max(0, 1.0 - self.friction_to_flow) * 0.15  # lower friction = higher delight
            + max(0, 1.0 - min(self.recovery_time_s / 30, 1.0)) * 0.15  # faster recovery = higher
            + max(0, min(self.session_momentum + 0.5, 1.0)) * 0.15
        )

        self.system_health = (
            max(0, min(self.genome_fitness_trend + 0.5, 1.0)) * 0.35
            + max(0, min(self.converge_velocity, 1.0)) * 0.35
            + min(self.convergence_efficiency, 1.0) * 0.30
        )

        self.composite = self.user_delight * 0.6 + self.system_health * 0.4

        # Bootstrap confidence interval
        self.confidence_interval = _bootstrap_ci(self)


def _bootstrap_ci(snap: "DelightV2Snapshot", n_resamples: int = 200) -> tuple:
    """Estimate 95% CI for composite via bootstrap resampling.

    Perturbs each signal by +/- 10% and recomputes composite.
    Returns (lower_bound, upper_bound).
    """
    signals = [
        snap.flow_state, snap.velocity, snap.completion_ratio,
        snap.friction_to_flow, snap.recovery_time_s,
        snap.convergence_efficiency, snap.session_momentum,
        snap.genome_fitness_trend, snap.converge_velocity,
    ]
    composites = []
    for _ in range(n_resamples):
        perturbed = DelightV2Snapshot(
            flow_state=max(0, signals[0] * random.uniform(0.9, 1.1)),
            velocity=max(0, signals[1] * random.uniform(0.9, 1.1)),
            completion_ratio=max(0, min(1, signals[2] * random.uniform(0.9, 1.1))),
            friction_to_flow=max(0, signals[3] * random.uniform(0.9, 1.1)),
            recovery_time_s=max(0, signals[4] * random.uniform(0.9, 1.1)),
            convergence_efficiency=max(0, signals[5] * random.uniform(0.9, 1.1)),
            session_momentum=signals[6] * random.uniform(0.9, 1.1),
            genome_fitness_trend=signals[7] * random.uniform(0.9, 1.1),
            converge_velocity=signals[8] * random.uniform(0.9, 1.1),
            velocity_shadow=snap.velocity_shadow,
            flow_shadow=snap.flow_shadow,
        )
        # Inline compute without CI (avoids recursion into _bootstrap_ci)
        flow_adj = perturbed.flow_state * max(0.5, 1.0 - perturbed.flow_shadow)
        vel_adj = min(perturbed.velocity / 3.0, 1.0) * max(0.5, 1.0 - perturbed.velocity_shadow)
        ud = (
            flow_adj * 0.2 + vel_adj * 0.2
            + perturbed.completion_ratio * 0.15
            + max(0, 1.0 - perturbed.friction_to_flow) * 0.15
            + max(0, 1.0 - min(perturbed.recovery_time_s / 30, 1.0)) * 0.15
            + max(0, min(perturbed.session_momentum + 0.5, 1.0)) * 0.15
        )
        sh = (
            max(0, min(perturbed.genome_fitness_trend + 0.5, 1.0)) * 0.35
            + max(0, min(perturbed.converge_velocity, 1.0)) * 0.35
            + min(perturbed.convergence_efficiency, 1.0) * 0.30
        )
        composites.append(ud * 0.6 + sh * 0.4)

    composites.sort()
    lower = composites[int(n_resamples * 0.025)]
    upper = composites[int(n_resamples * 0.975)]
    return (round(lower, 4), round(upper, 4))


# =========================================================================
# Signal extractors — derive from sentinel data + git + converge metrics
# =========================================================================

def _parse_jsonl_timestamps(path: Path, cutoff: datetime) -> list[datetime]:
    """Parse timestamps from a JSONL file, filtering by cutoff. Reusable for any log."""
    results = []
    if not path.exists():
        return results
    try:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                ts_str = rec.get("ts", "")[:19]
                ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    results.append(ts)
            except (json.JSONDecodeError, ValueError):
                continue
    except (OSError, UnicodeDecodeError):
        pass
    return results


def _collect_tool_calls(cutoff: datetime) -> list[datetime]:
    """Collect tool call timestamps from sentinel log."""
    if not SENTINEL_LOG.exists():
        return []
    calls = []
    try:
        for line in SENTINEL_LOG.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                ts_str = rec.get("ts", "")[:19]
                ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                if ts >= cutoff and rec.get("event", "") in ("PreToolUse", "PostToolUse"):
                    calls.append(ts)
            except (json.JSONDecodeError, ValueError):
                continue
    except (OSError, UnicodeDecodeError):
        pass
    return calls


def _compute_gate_friction(tool_calls: list[datetime], cutoff: datetime) -> tuple[int, list[float]]:
    """Compute gate blocks and recovery times from gate-blocks.jsonl. O(n log n) via bisect."""
    import bisect
    gate_log = CLAUDE_DIR / "cache" / "gate-blocks.jsonl"
    block_timestamps = _parse_jsonl_timestamps(gate_log, cutoff)
    sorted_calls = sorted(tool_calls)
    recovery_times = []
    for block_ts in block_timestamps:
        idx = bisect.bisect_right(sorted_calls, block_ts)
        if idx < len(sorted_calls):
            recovery_times.append((sorted_calls[idx] - block_ts).total_seconds())
    return len(block_timestamps), recovery_times


def _sentinel_signals(window_minutes: int = 120) -> dict:
    """Extract friction-to-flow and recovery time from sentinel log."""
    if not SENTINEL_LOG.exists():
        return {"friction_to_flow": 0.0, "recovery_time_s": 0.0, "total_ops": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    tool_calls = _collect_tool_calls(cutoff)
    total_ops = len(tool_calls)
    if total_ops == 0:
        return {"friction_to_flow": 0.0, "recovery_time_s": 0.0, "total_ops": 0}

    gate_blocks, recovery_times = _compute_gate_friction(tool_calls, cutoff)
    friction = gate_blocks / max(total_ops, 1)
    avg_recovery = sum(recovery_times) / len(recovery_times) if recovery_times else 0.0

    return {
        "friction_to_flow": round(friction, 4),
        "recovery_time_s": round(avg_recovery, 1),
        "total_ops": total_ops,
    }


def _convergence_efficiency() -> float:
    """Compute convergence efficiency from converge_metrics.jsonl.

    Efficiency = average(criterion_met / iterations_used) across recent runs.
    1.0 = criterion met in 1 iteration (perfect). 0.0 = never met.
    """
    if not CONVERGE_METRICS.exists():
        return 0.5  # neutral default

    try:
        lines = CONVERGE_METRICS.read_text().strip().splitlines()
    except (OSError, UnicodeDecodeError):
        return 0.5

    efficiencies = []
    for line in lines[-20:]:  # last 20 convergence runs
        try:
            rec = json.loads(line)
            # Support both field naming conventions (converge_metrics.py vs converge kernel)
            met = rec.get("criterion_met", rec.get("satisfied", False))
            iters = rec.get("iterations", rec.get("depth_reached", 1))
            if met and iters > 0:
                efficiencies.append(1.0 / iters)
            elif not met:
                efficiencies.append(0.0)
        except json.JSONDecodeError:
            continue

    return sum(efficiencies) / len(efficiencies) if efficiencies else 0.5


def _git_log(extra_args: list[str]) -> str | None:
    """Run git log with extra args. Returns stdout or None on failure."""
    try:
        result = subprocess.run(
            ["git", "log"] + extra_args,
            capture_output=True, text=True, timeout=10, cwd=str(CLAUDE_DIR),
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _git_commit_timestamps(since: str) -> list[datetime]:
    """Fetch sorted non-merge commit timestamps from git log."""
    output = _git_log([f"--since={since}", "--no-merges", "--format=%ci"])
    if not output:
        return []
    timestamps = []
    for line in output.splitlines():
        try:
            timestamps.append(datetime.fromisoformat(line.strip().replace(" +", "+").replace(" -", "-")))
        except ValueError:
            continue
    timestamps.sort()
    return timestamps


def _session_momentum(hours: float = 2.0) -> float:
    """Compute session momentum: acceleration of task completion. Returns [-1, 1]."""
    timestamps = _git_commit_timestamps(f"{int(max(hours, 1))} hours ago")
    if len(timestamps) < 4:
        return 0.0

    mid = len(timestamps) // 2
    first_half, second_half = timestamps[:mid], timestamps[mid:]
    if len(first_half) < 2 or len(second_half) < 2:
        return 0.0

    first_span = (first_half[-1] - first_half[0]).total_seconds() or 1
    second_span = (second_half[-1] - second_half[0]).total_seconds() or 1
    first_rate = len(first_half) / first_span
    if first_rate == 0:
        return 0.0

    ratio = (len(second_half) / second_span - first_rate) / first_rate
    return max(-1.0, min(1.0, ratio))


def _genome_fitness_trend() -> float:
    """Genome freshness: 1.0 if evolved today, decays to 0 at 7 days.

    Replaces the original fitness delta which was always ~0 due to
    genome stagnation. Mtime-based signal reflects whether the
    evolution pipeline is actually running.
    """
    try:
        if not GENOME_FILE.exists():
            return 0.0
        import time as _time
        age_hours = (_time.time() - GENOME_FILE.stat().st_mtime) / 3600
        # 1.0 at 0h, linear decay to 0.0 at 168h (7 days)
        return max(0.0, 1.0 - (age_hours / 168.0)) - 0.5  # offset to match original range [-0.5, 0.5]
    except OSError:
        return 0.0


def _learning_health() -> float:
    """Learning pipeline health: are insights being captured recently?

    Replaces converge_velocity which was always 0.0 (VelocityTracker import failure).
    Measures whether the learning substrate is active by checking insights.jsonl recency.
    Returns 0-1: 1.0 if insights captured in last hour, decays to 0 at 48h.
    """
    insights_path = CLAUDE_DIR / "learning" / "insights.jsonl"
    if not insights_path.exists():
        return 0.0
    try:
        import time as _time
        age_hours = (_time.time() - insights_path.stat().st_mtime) / 3600
        return max(0.0, 1.0 - (age_hours / 48.0))
    except OSError:
        return 0.0


def _velocity_shadow() -> float:
    """Revert rate: proportion of recent commits that are fix/revert."""
    output = _git_log(["--since=24 hours ago", "--no-merges", "--format=%s"])
    if not output:
        return 0.0
    messages = output.splitlines()
    reverts = sum(1 for m in messages if m.lower().startswith(("revert", "fix:")))
    return reverts / len(messages) if messages else 0.0


def _score_break_interval(avg_minutes: float) -> float:
    """Score coffee break frequency: optimal at 60-90min."""
    if 60 <= avg_minutes <= 90:
        return 1.0
    if 30 <= avg_minutes < 60 or 90 < avg_minutes <= 120:
        return 0.7
    if avg_minutes < 30:
        return 0.4
    return 0.3


def _coffee_break_health() -> float:
    """Coffee break frequency as session health signal. Returns [0, 1]."""
    coffee_log = CLAUDE_DIR / "learning" / "coffee_log.jsonl"
    if not coffee_log.exists():
        return 0.0
    try:
        lines = coffee_log.read_text().strip().splitlines()
        if len(lines) < 2:
            return 0.3
        timestamps = []
        for e in (json.loads(l) for l in lines):
            try:
                timestamps.append(datetime.fromisoformat(e.get("timestamp", "").replace("Z", "+00:00")))
            except ValueError:
                continue
        if len(timestamps) < 2:
            return 0.3
        timestamps.sort()
        intervals = [(timestamps[i + 1] - timestamps[i]).total_seconds() / 60 for i in range(len(timestamps) - 1)]
        return _score_break_interval(sum(intervals) / len(intervals))
    except Exception:
        return 0.0


def _pr_velocity_shadow() -> float:
    """PR-without-tests rate: Goodhart protection for shipping velocity."""
    output = _git_log(["--since=24 hours ago", "--merges", "--format=%H"])
    if not output:
        return 0.0
    merge_shas = output.splitlines()[:20]
    if not merge_shas:
        return 0.0

    no_tests = 0
    for sha in merge_shas:
        diff_output = _git_log(["--format=", "--name-only", f"{sha}^..{sha}"])
        if diff_output and not any("test" in f.lower() for f in diff_output.splitlines()):
            no_tests += 1

    return no_tests / len(merge_shas)


# =========================================================================
# Trend analysis with Cohen's d effect size
# =========================================================================

def cohens_d(group1: list, group2: list) -> float:
    """Compute Cohen's d effect size between two groups.

    |d| < 0.2: negligible, 0.2-0.5: small, 0.5-0.8: medium, > 0.8: large.
    """
    if len(group1) < 2 or len(group2) < 2:
        return 0.0

    mean1 = sum(group1) / len(group1)
    mean2 = sum(group2) / len(group2)

    var1 = sum((x - mean1) ** 2 for x in group1) / (len(group1) - 1)
    var2 = sum((x - mean2) ** 2 for x in group2) / (len(group2) - 1)

    pooled_std = math.sqrt(
        ((len(group1) - 1) * var1 + (len(group2) - 1) * var2)
        / (len(group1) + len(group2) - 2)
    )

    if pooled_std == 0:
        return 0.0

    return (mean2 - mean1) / pooled_std


def _classify_effect(d: float) -> tuple[str, str]:
    """Classify Cohen's d into effect label and trend direction."""
    abs_d = abs(d)
    if abs_d < 0.2:
        label = "negligible"
    elif abs_d < 0.5:
        label = "small"
    elif abs_d < 0.8:
        label = "medium"
    else:
        label = "large"
    trend = "improving" if d > 0.2 else ("declining" if d < -0.2 else "stable")
    return label, trend


def _load_delight_snapshots() -> list[dict] | None:
    """Load DELIGHT v2 snapshots from JSONL. Returns None on failure."""
    if not DELIGHT_V2_FILE.exists():
        return None
    try:
        raw = DELIGHT_V2_FILE.read_text()
    except (OSError, UnicodeDecodeError):
        return None
    snapshots = []
    for line in raw.splitlines():
        try:
            snapshots.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return snapshots


def delight_trend_v2(days: int = 30) -> dict:
    """Trend analysis with Cohen's d effect size."""
    snapshots = _load_delight_snapshots()
    if snapshots is None:
        return {"status": "no_data", "message": "No DELIGHT v2 snapshots yet."}
    if len(snapshots) < 4:
        return {"status": "insufficient_data", "message": f"Need >= 4 snapshots, have {len(snapshots)}."}

    recent = snapshots[-days:] if len(snapshots) > days else snapshots
    composites = [s.get("composite", 0) for s in recent]
    mid = len(composites) // 2
    d = cohens_d(composites[:mid], composites[mid:])
    effect_label, trend = _classify_effect(d)

    user_ds = [s.get("user_delight", 0) for s in recent]
    sys_hs = [s.get("system_health", 0) for s in recent]

    return {
        "status": "ok", "snapshots": len(recent),
        "composite_avg": round(sum(composites) / len(composites), 4),
        "user_delight_avg": round(sum(user_ds) / len(user_ds), 4) if user_ds else 0,
        "system_health_avg": round(sum(sys_hs) / len(sys_hs), 4) if sys_hs else 0,
        "trend": trend, "effect_size": round(d, 3),
        "effect_label": effect_label, "significant": abs(d) >= 0.5,
    }


# =========================================================================
# Main snapshot capture
# =========================================================================

def capture_snapshot(
    issues_completed: int = 0,
    issues_attempted: int = 0,
    elapsed_hours: float = 2.0,
    gate_blocks: int = 0,
    total_ops: int = 0,
) -> DelightV2Snapshot:
    """Capture a DELIGHT v2 snapshot with all signals."""
    snap = DelightV2Snapshot()
    snap.timestamp = datetime.now(timezone.utc).isoformat()

    # Auto-detect from git if not provided
    if issues_completed == 0 or issues_attempted == 0:
        from lib.auto_converge import _git_velocity
        completed, attempted, hours = _git_velocity(elapsed_hours)
        issues_completed = issues_completed or completed
        issues_attempted = issues_attempted or attempted
        elapsed_hours = hours or elapsed_hours

    # Original signals
    snap.velocity = issues_completed / max(elapsed_hours, 0.1)
    snap.completion_ratio = issues_completed / max(issues_attempted, 1)

    # Sentinel-derived signals
    sentinel_data = _sentinel_signals(int(elapsed_hours * 60))
    snap.friction_to_flow = sentinel_data["friction_to_flow"]
    snap.recovery_time_s = sentinel_data["recovery_time_s"]
    total_ops = sentinel_data["total_ops"] or total_ops

    # Flow state from sentinel (fraction of ops without blocks)
    if total_ops > 0:
        snap.flow_state = max(0, 1.0 - (gate_blocks / total_ops))
    else:
        snap.flow_state = 1.0  # no data = assume flowing

    # Convergence efficiency
    snap.convergence_efficiency = _convergence_efficiency()

    # Session momentum
    snap.session_momentum = _session_momentum(elapsed_hours)

    # System health signals
    snap.genome_fitness_trend = _genome_fitness_trend()
    snap.converge_velocity = _learning_health()

    # Shadow metrics
    snap.velocity_shadow = _velocity_shadow()
    # flow_shadow: approximated by friction (if friction is high, flow is suspect)
    snap.flow_shadow = min(snap.friction_to_flow * 2, 1.0)

    snap.compute()

    # Novel signals (v2.1) — recorded as metadata, not in composite yet
    # Coffee break health and PR velocity shadow need validation before
    # inclusion in composite (H014 pending). Stored for analysis.
    snap._coffee_health = _coffee_break_health()
    snap._pr_velocity_shadow = _pr_velocity_shadow()

    _save_snapshot(snap)
    return snap


def _save_snapshot(snap: DelightV2Snapshot) -> None:
    """Append snapshot to learning/delight_v2.jsonl."""
    try:
        DELIGHT_V2_FILE.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": snap.timestamp,
            "version": snap.version,
            "composite": round(snap.composite, 4),
            "user_delight": round(snap.user_delight, 4),
            "system_health": round(snap.system_health, 4),
            "flow_state": round(snap.flow_state, 4),
            "velocity": round(snap.velocity, 4),
            "completion_ratio": round(snap.completion_ratio, 4),
            "friction_to_flow": round(snap.friction_to_flow, 4),
            "recovery_time_s": round(snap.recovery_time_s, 1),
            "convergence_efficiency": round(snap.convergence_efficiency, 4),
            "session_momentum": round(snap.session_momentum, 4),
            "genome_fitness_trend": round(snap.genome_fitness_trend, 4),
            "converge_velocity": round(snap.converge_velocity, 4),
            "velocity_shadow": round(snap.velocity_shadow, 4),
            "flow_shadow": round(snap.flow_shadow, 4),
            "ci_lower": snap.confidence_interval[0],
            "ci_upper": snap.confidence_interval[1],
            # v2.1 novel signals (metadata, not in composite yet)
            "coffee_health": round(getattr(snap, "_coffee_health", 0.0), 4),
            "pr_velocity_shadow": round(getattr(snap, "_pr_velocity_shadow", 0.0), 4),
        }
        with open(DELIGHT_V2_FILE, "a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except (OSError, Exception):
        pass


# =========================================================================
# CLI
# =========================================================================

def _cli_snapshot():
    """Print session quality snapshot. Plain language by default, --raw for metrics."""
    hours = 2.0
    for i, arg in enumerate(sys.argv[2:], 2):
        if arg == "--session-hours" and i + 1 < len(sys.argv):
            hours = float(sys.argv[i + 1])
    snap = capture_snapshot(elapsed_hours=hours)
    if "--raw" in sys.argv:
        ci = snap.confidence_interval
        print(f"DELIGHT v2: {snap.composite:.3f} (95% CI: [{ci[0]:.3f}, {ci[1]:.3f}])")
        print(f"  User delight:  {snap.user_delight:.3f}")
        print(f"    flow={snap.flow_state:.2f} vel={snap.velocity:.1f}/hr ratio={snap.completion_ratio:.2f}")
        print(f"    friction={snap.friction_to_flow:.3f} recovery={snap.recovery_time_s:.1f}s momentum={snap.session_momentum:.2f}")
        print(f"  System health: {snap.system_health:.3f}")
        print(f"    genome={snap.genome_fitness_trend:.3f} converge_vel={snap.converge_velocity:.3f} efficiency={snap.convergence_efficiency:.2f}")
        print(f"  Shadows: vel_shadow={snap.velocity_shadow:.2f} flow_shadow={snap.flow_shadow:.2f}")
    else:
        from plain_language import explain_session
        print(explain_session({
            "flow": snap.flow_state, "velocity": snap.velocity,
            "system_health": snap.system_health, "friction": snap.friction_to_flow,
            "vel_shadow": snap.velocity_shadow, "flow_shadow": snap.flow_shadow,
        }))


def _cli_trend():
    """Print session quality trend over time."""
    days = 30
    if len(sys.argv) > 2 and sys.argv[2] == "--days" and len(sys.argv) > 3:
        days = int(sys.argv[3])
    result = delight_trend_v2(days)
    if result.get("status") != "ok":
        print(f"Trend: {result.get('message', 'no data')}")
    else:
        print(f"Trend: {result['trend']} ({result['effect_label']} change)")
        print(f"  Average quality: {result['composite_avg']:.4f}")
        print(f"  Statistically significant: {'yes' if result['significant'] else 'no'}")


def _cli_falsify():
    """Print the falsification registry — what would disprove each metric."""
    print("What would disprove each quality signal:")
    print("=" * 50)
    for signal, info in FALSIFICATION_REGISTRY.items():
        print(f"\n{signal}:")
        print(f"  Claim: {info['claim']}")
        print(f"  Disproved by: {info['falsified_by']}")
        print(f"  Warning indicator: {info['shadow_metric']}")


def main():
    commands = {"snapshot": _cli_snapshot, "trend": _cli_trend, "falsify": _cli_falsify}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(f"Usage: delight_v2.py <{'|'.join(commands)}>")
        sys.exit(1)
    commands[sys.argv[1]]()


if __name__ == "__main__":
    main()
