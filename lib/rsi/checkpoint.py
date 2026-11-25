#!/usr/bin/env python3
"""RSI checkpoint/resume — mid-cycle recovery for RSI loops.

Includes closeout chain (Gen 487): auto-runs /evo + /learn + /save after
each successful MERGE phase. Each step is error-isolated — failure of one
does not block the others.
"""

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config
from .metrics import record_metric

logger = logging.getLogger(__name__)


@dataclass
class RSICheckpoint:
    """Persisted RSI cycle state for mid-cycle recovery.

    Saved after each phase completion so a fresh session can resume
    from the last successful phase instead of restarting the cycle.
    """
    phase: str  # detect, prioritize, implement, test, review, merge
    candidate_id: str = ""
    candidate_title: str = ""
    branch: str = ""
    issue_number: int = 0
    pr_number: int = 0
    ralph_score: int = 0
    attempt: int = 0  # Review retry count
    state_data: dict = field(default_factory=dict)
    timestamp: str = ""
    session_id: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


def save_checkpoint(
    phase: str,
    candidate_id: str = "",
    candidate_title: str = "",
    branch: str = "",
    issue_number: int = 0,
    pr_number: int = 0,
    ralph_score: int = 0,
    attempt: int = 0,
    state_data: dict = None,
) -> RSICheckpoint:
    """Save RSI checkpoint after a phase completion.

    Args:
        phase: Current RSI phase (detect, prioritize, implement, test, review, merge).
        candidate_id: ID of the candidate being worked on.
        candidate_title: Human-readable title.
        branch: Git branch name (if created).
        issue_number: GitHub issue number.
        pr_number: PR number (if created).
        ralph_score: Last Ralph review score.
        attempt: Review retry count.
        state_data: Arbitrary phase-specific data.

    Returns:
        The saved RSICheckpoint.
    """
    checkpoint = RSICheckpoint(
        phase=phase,
        candidate_id=candidate_id,
        candidate_title=candidate_title,
        branch=branch,
        issue_number=issue_number,
        pr_number=pr_number,
        ralph_score=ralph_score,
        attempt=attempt,
        state_data=state_data or {},
        session_id=os.environ.get("GRIP_INSTANCE_ID", ""),
    )
    config.RSI_CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.RSI_CHECKPOINT_FILE.write_text(json.dumps(asdict(checkpoint), indent=2))
    record_metric("rsi_checkpoint_saved", {
        "phase": phase,
        "candidate_id": candidate_id,
    })
    return checkpoint


def load_checkpoint() -> Optional[RSICheckpoint]:
    """Load existing RSI checkpoint, if any.

    Returns None if:
    - No checkpoint file exists
    - Checkpoint is stale (>24 hours old)
    - Checkpoint file is corrupt

    Returns:
        RSICheckpoint or None.
    """
    if not config.RSI_CHECKPOINT_FILE.exists():
        return None

    try:
        data = json.loads(config.RSI_CHECKPOINT_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        # Corrupt file — clear it
        config.RSI_CHECKPOINT_FILE.unlink(missing_ok=True)
        record_metric("rsi_checkpoint_corrupt_cleaned", {})
        return None

    # Check staleness
    ts = data.get("timestamp", "")
    if ts:
        try:
            checkpoint_dt = datetime.fromisoformat(ts)
            age_hours = (datetime.now(timezone.utc) - checkpoint_dt).total_seconds() / 3600
            if age_hours > config.RSI_CHECKPOINT_STALE_HOURS:
                config.RSI_CHECKPOINT_FILE.unlink(missing_ok=True)
                record_metric("rsi_checkpoint_stale_discarded", {
                    "age_hours": round(age_hours, 1),
                    "phase": data.get("phase", "?"),
                })
                return None
        except (ValueError, TypeError):
            pass  # Can't parse timestamp — proceed with checkpoint

    return RSICheckpoint(
        phase=data.get("phase", ""),
        candidate_id=data.get("candidate_id", ""),
        candidate_title=data.get("candidate_title", ""),
        branch=data.get("branch", ""),
        issue_number=data.get("issue_number", 0),
        pr_number=data.get("pr_number", 0),
        ralph_score=data.get("ralph_score", 0),
        attempt=data.get("attempt", 0),
        state_data=data.get("state_data", {}),
        timestamp=data.get("timestamp", ""),
        session_id=data.get("session_id", ""),
    )


def clear_checkpoint() -> bool:
    """Clear the RSI checkpoint after successful cycle completion.

    Returns True if a checkpoint was cleared, False if none existed.
    """
    if not config.RSI_CHECKPOINT_FILE.exists():
        return False

    try:
        data = json.loads(config.RSI_CHECKPOINT_FILE.read_text())
        phase = data.get("phase", "?")
        candidate = data.get("candidate_id", "?")
    except (json.JSONDecodeError, OSError):
        phase = "?"
        candidate = "?"

    config.RSI_CHECKPOINT_FILE.unlink(missing_ok=True)
    record_metric("rsi_checkpoint_cleared", {
        "completed_phase": phase,
        "candidate_id": candidate,
    })
    return True


def checkpoint_resume_phase(checkpoint: RSICheckpoint) -> str:
    """Determine which phase to resume from based on checkpoint.

    The resume phase is the NEXT phase after the checkpointed one,
    since the checkpoint records the last *completed* phase.

    Returns:
        Phase name to resume from.
    """
    phase_order = ["detect", "prioritize", "implement", "test", "review", "merge"]
    try:
        idx = phase_order.index(checkpoint.phase)
        if idx + 1 < len(phase_order):
            return phase_order[idx + 1]
        # Completed merge — cycle is done
        return "done"
    except ValueError:
        # Unknown phase — restart from beginning
        return "detect"


# ── Closeout Chain (Gen 487 — Architecture Synthesis Council P1) ────

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

_CLOSEOUT_STEPS = [
    {
        "name": "evo",
        "label": "Fractal facet evolution",
        "cmd": [sys.executable, str(CLAUDE_DIR / "lib" / "evo.py"), "run", "5"],
        "timeout": 300,
    },
    {
        "name": "learn",
        "label": "Insight consolidation",
        "cmd": [sys.executable, str(CLAUDE_DIR / "lib" / "learning_detector.py"), "consolidate"],
        "timeout": 60,
    },
    {
        "name": "save",
        "label": "Instance state persistence",
        "cmd": [sys.executable, str(CLAUDE_DIR / "lib" / "instance_serializer.py"), "auto",
                "--achievement", "RSI closeout auto-save"],
        "timeout": 30,
    },
]


@dataclass
class CloseoutResult:
    """Result of a single closeout step."""
    name: str
    success: bool
    elapsed_sec: float = 0.0
    error: str = ""


def run_closeout(candidate_id: str = "", dry_run: bool = False) -> list[CloseoutResult]:
    """Run the post-MERGE closeout chain: /evo -> /learn -> /save.

    Each step runs independently — failure of one does not block the others.
    Results are recorded as metrics and logged to the checkpoint state.

    Args:
        candidate_id: ID of the just-merged candidate (for metric tagging).
        dry_run: If True, log steps without executing.

    Returns:
        List of CloseoutResult for each step.
    """
    results: list[CloseoutResult] = []
    closeout_start = datetime.now(timezone.utc)

    logger.info("[closeout] Starting post-MERGE closeout chain (%d steps)", len(_CLOSEOUT_STEPS))

    for step in _CLOSEOUT_STEPS:
        step_start = datetime.now(timezone.utc)

        if dry_run:
            logger.info("[closeout] DRY RUN: %s (%s)", step["name"], step["label"])
            results.append(CloseoutResult(name=step["name"], success=True))
            continue

        try:
            logger.info("[closeout] Running %s: %s", step["name"], step["label"])
            step_timeout = step.get("timeout", 120)
            proc = subprocess.run(
                step["cmd"],
                capture_output=True,
                text=True,
                timeout=step_timeout,
                cwd=str(CLAUDE_DIR),
            )
            elapsed = (datetime.now(timezone.utc) - step_start).total_seconds()

            if proc.returncode == 0:
                logger.info("[closeout] %s completed (%.1fs)", step["name"], elapsed)
                results.append(CloseoutResult(
                    name=step["name"], success=True, elapsed_sec=elapsed,
                ))
            else:
                error_msg = (proc.stderr or proc.stdout or "")[:200]
                logger.warning(
                    "[closeout] %s failed (rc=%d, %.1fs): %s",
                    step["name"], proc.returncode, elapsed, error_msg,
                )
                results.append(CloseoutResult(
                    name=step["name"], success=False,
                    elapsed_sec=elapsed, error=error_msg,
                ))

        except subprocess.TimeoutExpired:
            elapsed = (datetime.now(timezone.utc) - step_start).total_seconds()
            logger.warning("[closeout] %s timed out after %.1fs", step["name"], elapsed)
            results.append(CloseoutResult(
                name=step["name"], success=False,
                elapsed_sec=elapsed, error="timeout (120s)",
            ))

        except OSError as e:
            elapsed = (datetime.now(timezone.utc) - step_start).total_seconds()
            logger.warning("[closeout] %s OS error: %s", step["name"], e)
            results.append(CloseoutResult(
                name=step["name"], success=False,
                elapsed_sec=elapsed, error=str(e),
            ))

    # Record aggregate metric
    total_elapsed = (datetime.now(timezone.utc) - closeout_start).total_seconds()
    succeeded = sum(1 for r in results if r.success)
    record_metric("rsi_closeout_completed", {
        "candidate_id": candidate_id,
        "steps_succeeded": succeeded,
        "steps_total": len(results),
        "elapsed_sec": round(total_elapsed, 1),
        "details": {r.name: {"ok": r.success, "error": r.error} for r in results},
    })

    logger.info(
        "[closeout] Finished: %d/%d steps succeeded (%.1fs total)",
        succeeded, len(results), total_elapsed,
    )

    return results
