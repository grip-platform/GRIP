#!/usr/bin/env python3
"""
Ultrado Checkpoint — Crash-resilient state persistence between waves.

Saves wave state after each completion so ultrado can resume from the
last good checkpoint after a crash. Prevents the cascade failure mode
where 4+ sessions die without saving any progress.

Origin: Gen 499. 4 sessions crashed from lembed SIGSEGV with zero recovery.
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path.home() / ".claude"
CHECKPOINT_DIR = CLAUDE_DIR / "state" / "ultrado"
EXPIRY_HOURS = 48


def _get_git_sha() -> str:
    """Get current git HEAD SHA (short)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(CLAUDE_DIR),
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def checkpoint_wave(
    run_id: str,
    wave_num: int,
    goal: str,
    plan_file: Optional[str] = None,
    completed_tasks: Optional[list] = None,
    pending_tasks: Optional[list] = None,
    hypotheses: Optional[list] = None,
    precision_levels: Optional[dict] = None,
    wave_results: Optional[dict] = None,
) -> Path:
    """
    Save wave state for crash recovery.

    Called at the end of each wave's SHIP phase, before LEARN.
    Returns the path to the checkpoint file.
    """
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    checkpoint = {
        "run_id": run_id,
        "wave_num": wave_num,
        "goal": goal,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": _get_git_sha(),
        "plan_file": plan_file,
        "completed_tasks": completed_tasks or [],
        "pending_tasks": pending_tasks or [],
        "hypotheses": hypotheses or [],
        "precision_levels": precision_levels or {},
        "wave_results": wave_results or {},
    }

    path = CHECKPOINT_DIR / f"{run_id}.json"
    with open(path, "w") as f:
        json.dump(checkpoint, f, indent=2)

    return path


def resume_from_checkpoint(run_id: Optional[str] = None) -> Optional[dict]:
    """
    Resume from latest checkpoint.

    Args:
        run_id: Specific run to resume. If None, finds the most recent.

    Returns:
        Checkpoint dict, or None if no valid checkpoint exists.
    """
    if not CHECKPOINT_DIR.exists():
        return None

    if run_id:
        path = CHECKPOINT_DIR / f"{run_id}.json"
        if not path.exists():
            return None
        return _load_checkpoint(path)

    # Find most recent non-expired checkpoint
    checkpoints = sorted(
        CHECKPOINT_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    for path in checkpoints:
        checkpoint = _load_checkpoint(path)
        if checkpoint and not _is_expired(checkpoint):
            return checkpoint

    return None


def list_checkpoints() -> list:
    """List all checkpoints with summary info."""
    if not CHECKPOINT_DIR.exists():
        return []

    results = []
    for path in sorted(CHECKPOINT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        checkpoint = _load_checkpoint(path)
        if checkpoint:
            results.append({
                "run_id": checkpoint["run_id"],
                "wave_num": checkpoint["wave_num"],
                "goal": checkpoint.get("goal", "unknown")[:80],
                "timestamp": checkpoint["timestamp"],
                "git_sha": checkpoint.get("git_sha", "unknown"),
                "expired": _is_expired(checkpoint),
                "completed_count": len(checkpoint.get("completed_tasks", [])),
                "pending_count": len(checkpoint.get("pending_tasks", [])),
            })
    return results


def clear_checkpoint(run_id: str) -> bool:
    """Remove a checkpoint after successful completion."""
    path = CHECKPOINT_DIR / f"{run_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def prune_expired() -> int:
    """Remove checkpoints older than EXPIRY_HOURS. Returns count removed."""
    if not CHECKPOINT_DIR.exists():
        return 0

    removed = 0
    for path in CHECKPOINT_DIR.glob("*.json"):
        checkpoint = _load_checkpoint(path)
        if checkpoint and _is_expired(checkpoint):
            path.unlink()
            removed += 1
    return removed


def _load_checkpoint(path: Path) -> Optional[dict]:
    """Load and validate a checkpoint file."""
    try:
        with open(path) as f:
            data = json.load(f)
        if "run_id" in data and "wave_num" in data:
            return data
        return None
    except (json.JSONDecodeError, OSError):
        return None


def _is_expired(checkpoint: dict) -> bool:
    """Check if checkpoint is older than EXPIRY_HOURS."""
    try:
        ts = datetime.fromisoformat(checkpoint["timestamp"])
        age = datetime.now(timezone.utc) - ts
        return age.total_seconds() > EXPIRY_HOURS * 3600
    except (KeyError, ValueError):
        return True


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: ultrado_checkpoint.py [list|prune|resume [run_id]]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        for cp in list_checkpoints():
            expired = " [EXPIRED]" if cp["expired"] else ""
            print(
                f"  {cp['run_id'][:12]} | W{cp['wave_num']} | "
                f"{cp['completed_count']} done, {cp['pending_count']} pending | "
                f"{cp['timestamp'][:19]}{expired}"
            )
            print(f"    Goal: {cp['goal']}")

    elif cmd == "prune":
        n = prune_expired()
        print(f"Pruned {n} expired checkpoint(s)")

    elif cmd == "resume":
        rid = sys.argv[2] if len(sys.argv) > 2 else None
        cp = resume_from_checkpoint(rid)
        if cp:
            print(json.dumps(cp, indent=2))
        else:
            print("No checkpoint found")
            sys.exit(1)
