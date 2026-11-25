"""Safety rails for the automatic improvement loop.

Provides budget caps, cooldowns, kill switch, and audit logging
to prevent runaway self-modification.

Part of the GRIP automatic improvement MVP (Gen 276).
"""

import json
import time
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
KILL_SWITCH = CLAUDE_DIR / "state" / "rsi" / "no-auto-improve"
BUDGET_FILE = CLAUDE_DIR / "state" / "rsi" / "improvement-budget.json"
AUDIT_LOG = CLAUDE_DIR / "data" / "auto-improvement-log.jsonl"

MAX_ACTIONS_PER_SESSION = 5
COOLDOWN_SECONDS = 600  # 10 minutes between auto-improvement batches


def is_killed() -> bool:
    """Check if kill switch is active."""
    return KILL_SWITCH.exists()


def _load_budget() -> dict:
    if BUDGET_FILE.exists():
        try:
            return json.loads(BUDGET_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"actions_used": 0, "last_action_at": 0, "session_id": ""}


def _save_budget(budget: dict):
    BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    BUDGET_FILE.write_text(json.dumps(budget, indent=2))


def can_auto_improve(session_id: str = "") -> tuple:
    """Check if auto-improvement is allowed.

    Returns:
        (allowed: bool, reason: str)
    """
    if is_killed():
        return False, "Kill switch active (~/.claude/.no-auto-improve exists)"

    budget = _load_budget()

    # Reset budget for new session
    if session_id and budget.get("session_id") != session_id:
        budget = {
            "actions_used": 0,
            "last_action_at": 0,
            "session_id": session_id,
        }
        _save_budget(budget)

    if budget["actions_used"] >= MAX_ACTIONS_PER_SESSION:
        return (
            False,
            f"Budget exhausted ({MAX_ACTIONS_PER_SESSION}/{MAX_ACTIONS_PER_SESSION} actions used this session)",
        )

    elapsed = time.time() - budget.get("last_action_at", 0)
    if elapsed < COOLDOWN_SECONDS and budget["actions_used"] > 0:
        remaining = int(COOLDOWN_SECONDS - elapsed)
        return False, f"Cooldown active ({remaining}s remaining)"

    return True, "OK"


def record_action(
    session_id: str, action_type: str, description: str, success: bool
):
    """Record an auto-improvement action for budget and audit tracking."""
    budget = _load_budget()
    budget["actions_used"] = budget.get("actions_used", 0) + 1
    budget["last_action_at"] = time.time()
    budget["session_id"] = session_id
    _save_budget(budget)

    # Append to audit log
    entry = {
        "timestamp": time.time(),
        "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id,
        "action_type": action_type,
        "description": description,
        "success": success,
        "budget_remaining": MAX_ACTIONS_PER_SESSION - budget["actions_used"],
    }
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def remaining_budget(session_id: str = "") -> int:
    """Return number of auto-improvement actions remaining in budget."""
    budget = _load_budget()
    if session_id and budget.get("session_id") != session_id:
        return MAX_ACTIONS_PER_SESSION
    return max(0, MAX_ACTIONS_PER_SESSION - budget.get("actions_used", 0))


def reset_budget(session_id: str = ""):
    """Reset the budget (called at session start)."""
    _save_budget({
        "actions_used": 0,
        "last_action_at": 0,
        "session_id": session_id,
    })


# CLI interface
if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    sid = sys.argv[2] if len(sys.argv) > 2 else ""

    if cmd == "status":
        allowed, reason = can_auto_improve(sid)
        remaining = remaining_budget(sid)
        killed = is_killed()
        print(f"Kill switch: {'ACTIVE' if killed else 'off'}")
        print(f"Allowed: {allowed} ({reason})")
        print(f"Budget remaining: {remaining}/{MAX_ACTIONS_PER_SESSION}")
    elif cmd == "reset":
        reset_budget(sid)
        print("Budget reset")
    elif cmd == "kill":
        KILL_SWITCH.touch()
        print("Kill switch activated")
    elif cmd == "unkill":
        if KILL_SWITCH.exists():
            KILL_SWITCH.unlink()
        print("Kill switch deactivated")
    else:
        print(f"Usage: improvement_safety.py [status|reset|kill|unkill] [session_id]")
