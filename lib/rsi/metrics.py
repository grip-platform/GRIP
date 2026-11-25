#!/usr/bin/env python3
"""RSI metrics recording — event logging, session detection, failure categorization.

NOTE: This module must NOT import from queue, budget, or convergence at module level
to avoid circular imports. get_session_summary() uses lazy imports inside the function body.
"""

import json
import os
from datetime import datetime, timezone

from . import config


def detect_session_type() -> str:
    """Detect current session type from environment signals.

    Returns:
        "autonomous" — GRIP_AUTONOMOUS env var set >= 1
        "copilot"    — Running as copilot worker (agent subprocess)
        "interactive" — Default: human-driven session
    """
    # Check autonomous mode — value may be "1", "2", or "grip-auto-{hash}:2"
    grip_auto = os.environ.get("GRIP_AUTONOMOUS", "")
    if grip_auto:
        # Extract numeric level from various formats
        try:
            autonomous_level = int(grip_auto)
        except ValueError:
            # Format: "grip-auto-{hash}:{level}" — extract trailing number
            parts = grip_auto.rsplit(":", 1)
            try:
                autonomous_level = int(parts[-1]) if len(parts) > 1 else 1
            except ValueError:
                autonomous_level = 1  # Any non-empty value means autonomous
        if autonomous_level >= 1:
            return "autonomous"

    # Check autonomous state file
    state_file = config.CLAUDE_DIR / "state" / "session" / "autonomous-state.json"
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text())
            if data.get("level", 0) >= 1:
                return "autonomous"
        except (json.JSONDecodeError, OSError):
            pass

    # Check copilot worker signal
    if os.environ.get("GRIP_COPILOT_WORKER"):
        return "copilot"

    return "interactive"


def categorize_failure(error: str) -> str:
    """Categorize a failure reason from error text.

    Returns one of: timeout, budget, context, test_failure, rate_limit,
    hook_blocked, git_error, import_error, unknown
    """
    error_lower = error.lower() if error else ""

    if "timeout" in error_lower or "timed out" in error_lower:
        return "timeout"
    if "budget" in error_lower or "rate limit" in error_lower or "429" in error_lower:
        return "rate_limit"
    if "context" in error_lower or "token" in error_lower or "window" in error_lower:
        return "context"
    if "test" in error_lower and ("fail" in error_lower or "error" in error_lower):
        return "test_failure"
    if "gate" in error_lower or "blocked" in error_lower or "denied" in error_lower:
        return "hook_blocked"
    if "git" in error_lower and ("conflict" in error_lower or "error" in error_lower):
        return "git_error"
    if "import" in error_lower or "module" in error_lower:
        return "import_error"

    return "unknown"


def record_metric(event: str, data: dict = None, failure_reason: str = None):
    """Append an RSI metric event to the metrics JSONL file.

    Automatically enriches with session_type and optional failure categorization.

    Args:
        event: Event name (e.g., "cycle_start", "candidate_completed").
        data: Additional event data.
        failure_reason: Optional error text — auto-categorized into failure type.
    """
    config.METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    enriched_data = data or {}
    enriched_data["session_type"] = detect_session_type()

    if failure_reason:
        enriched_data["failure_category"] = categorize_failure(failure_reason)
        enriched_data["failure_reason"] = failure_reason[:200]  # Truncate long errors

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "data": enriched_data,
    }
    with open(config.METRICS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def get_session_summary() -> dict:
    """Generate summary of current RSI session.

    Uses lazy imports to avoid circular dependency with convergence/queue.
    """
    from .convergence import load_convergence, check_stop_signal
    from .queue import load_queue

    convergence = load_convergence()
    queue = load_queue()

    pending = len([c for c in queue if c.status == "pending"])
    completed = len([c for c in queue if c.status == "completed"])
    skipped = len([c for c in queue if c.status == "skipped"])

    return {
        "convergence": {
            "avg_score": convergence.avg_score,
            "is_stalled": convergence.is_stalled,
            "stall_count": convergence.stall_count,
            "recent_scores": convergence.recent_scores[-5:],
        },
        "queue": {
            "pending": pending,
            "completed": completed,
            "skipped": skipped,
            "total": len(queue),
        },
        "stop_requested": check_stop_signal(),
    }
