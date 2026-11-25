"""CC Task Metrics for Genome Fitness (#1154).

Captures task completion/failure counts from the session and updates
genome fitness with a `task_efficiency` dimension. Reads and writes
the genome JSON format used by data/genomes/*.json.

Usage:
    from lib.cc_task_metrics import record_task, get_session_metrics, update_genome_fitness

    record_task("completed")        # or "failed", "skipped"
    metrics = get_session_metrics()
    update_genome_fitness("laurie", metrics)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
SESSION_METRICS_FILE = CLAUDE_DIR / "state" / "rsi" / "task-metrics.json"
GENOMES_DIR = CLAUDE_DIR / "data" / "genomes"

VALID_OUTCOMES = ("completed", "failed", "skipped")


def _load_session_metrics() -> dict:
    """Load current session task metrics from disk."""
    if SESSION_METRICS_FILE.exists():
        try:
            return json.loads(SESSION_METRICS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"tasks": [], "session_start": time.time()}


def _save_session_metrics(metrics: dict) -> None:
    """Persist session metrics to disk."""
    SESSION_METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_METRICS_FILE.write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def record_task(
    outcome: str,
    task_name: str = "",
    duration_seconds: float = 0.0,
    now: float | None = None,
) -> dict:
    """Record a single task outcome.

    Args:
        outcome: One of "completed", "failed", "skipped".
        task_name: Optional label for the task.
        duration_seconds: How long the task took.
        now: Override timestamp for testing.

    Returns:
        Summary dict with current counts.
    """
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"Invalid outcome '{outcome}'. Must be one of {VALID_OUTCOMES}")

    if now is None:
        now = time.time()

    metrics = _load_session_metrics()
    metrics["tasks"].append({
        "outcome": outcome,
        "task_name": task_name,
        "duration_seconds": duration_seconds,
        "timestamp": now,
    })
    _save_session_metrics(metrics)

    return _summarise(metrics)


def get_session_metrics() -> dict:
    """Return summarised metrics for the current session."""
    metrics = _load_session_metrics()
    return _summarise(metrics)


def _summarise(metrics: dict) -> dict:
    """Compute summary statistics from raw task list."""
    tasks = metrics.get("tasks", [])
    completed = sum(1 for t in tasks if t["outcome"] == "completed")
    failed = sum(1 for t in tasks if t["outcome"] == "failed")
    skipped = sum(1 for t in tasks if t["outcome"] == "skipped")
    total = len(tasks)
    total_duration = sum(t.get("duration_seconds", 0.0) for t in tasks)

    efficiency = completed / total if total > 0 else 0.0

    return {
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "total": total,
        "efficiency": round(efficiency, 4),
        "total_duration_seconds": round(total_duration, 2),
    }


def compute_task_efficiency_score(metrics: dict) -> float:
    """Convert task metrics into a 0.0-1.0 fitness score.

    Formula: efficiency * (1 - failure_penalty)
    where failure_penalty = failed / (total * 2) capped at 0.5

    This means:
    - 100% completion, 0 failures -> 1.0
    - 80% completion, 0 failures -> 0.8
    - 100% completion with some failures -> reduced by penalty
    - 0 tasks -> 0.0
    """
    total = metrics.get("total", 0)
    if total == 0:
        return 0.0

    efficiency = metrics.get("efficiency", 0.0)
    failed = metrics.get("failed", 0)
    failure_penalty = min(failed / (total * 2), 0.5)

    return round(max(0.0, efficiency * (1.0 - failure_penalty)), 4)


def update_genome_fitness(
    genome_name: str,
    metrics: dict,
    genomes_dir: Path | None = None,
) -> dict:
    """Update a genome file with the task_efficiency fitness dimension.

    Reads the genome JSON, adds/updates the `task_efficiency` key in
    `domain_fitness` for every gene, and writes back.

    Args:
        genome_name: Name of the genome file (without .json).
        metrics: Output from get_session_metrics().
        genomes_dir: Override genomes directory for testing.

    Returns:
        Summary of the update.
    """
    if genomes_dir is None:
        genomes_dir = GENOMES_DIR

    genome_path = genomes_dir / f"{genome_name}.json"
    if not genome_path.exists():
        return {"updated": False, "reason": f"genome file not found: {genome_path}"}

    score = compute_task_efficiency_score(metrics)
    counter = {"n": 0}

    def _apply(genome: dict) -> dict:
        for gene_data in genome.get("genes", {}).values():
            if not isinstance(gene_data, dict):
                continue
            if "domain_fitness" not in gene_data:
                gene_data["domain_fitness"] = {}
            gene_data["domain_fitness"]["task_efficiency"] = score
            counter["n"] += 1
        return genome

    # #3462 bug #3: lock+atomic read-modify-write so concurrent sessions can't
    # lose the update (was a raw read + write_text race on the shared genome).
    from genome_store import update_genome as _locked_update, LockTimeoutError

    try:
        _locked_update(genome_path, _apply)
    except (json.JSONDecodeError, OSError, LockTimeoutError) as e:
        return {"updated": False, "reason": str(e)}

    return {
        "updated": True,
        "genome": genome_name,
        "score": score,
        "genes_updated": counter["n"],
    }


def clear_session_metrics() -> dict:
    """Clear session metrics. Called at session end or on reset."""
    if SESSION_METRICS_FILE.exists():
        SESSION_METRICS_FILE.unlink()
        return {"cleared": True}
    return {"cleared": False, "reason": "no metrics file"}
