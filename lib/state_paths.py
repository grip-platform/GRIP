"""Canonical state file paths -- single source of truth (DRY).

All runtime state files live under CLAUDE_DIR/state/category/.
Import from here instead of hardcoding dotfile paths.

GRASP Information Expert: one authoritative module owns path knowledge.
"""
from __future__ import annotations

import os
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR_OVERRIDE", str(Path.home() / ".claude")))
STATE_DIR = CLAUDE_DIR / "state"

# --- RSI ---
RSI_STOP_SIGNAL = STATE_DIR / "rsi" / "rsi-stop-signal"
RSI_LAST_COLLECT = STATE_DIR / "rsi" / "rsi-last-collect.json"
RSI_ACTIVE_LOCK = STATE_DIR / "rsi" / "rsi-active.lock"
RSI_CHECKPOINT = STATE_DIR / "rsi" / "rsi-checkpoint.json"
RSI_CONVERGENCE = STATE_DIR / "rsi" / "rsi-convergence-state.json"
RSI_DELEGATE = STATE_DIR / "rsi" / "rsi-delegate-state.json"
RSI_SCHEDULE = STATE_DIR / "rsi" / "rsi-schedule.json"
RSI_SYNC = STATE_DIR / "rsi" / "rsi-sync-state.json"
RSI_SLACK_QUEUE = STATE_DIR / "rsi" / "rsi-slack-queue.json"
RSI_AUTO_CONVERGE = STATE_DIR / "rsi" / "auto-converge-state.json"
RSI_NO_AUTO_IMPROVE = STATE_DIR / "rsi" / "no-auto-improve"
RSI_PHASE_SCOPE = STATE_DIR / "rsi" / "phase-scope-state.json"
RSI_PHASE_HANDOFF = STATE_DIR / "rsi" / "phase-handoff.json"
RSI_EVO_LAST_RUN = STATE_DIR / "rsi" / "evo-last-run"
RSI_HEARTBEAT_LAST = STATE_DIR / "rsi" / "grip-heartbeat-last"
RSI_IMPROVEMENT_QUEUE = STATE_DIR / "rsi" / "improvement-queue.json"

# --- Session ---
SESSION_AUTONOMOUS = STATE_DIR / "session" / "autonomous-state.json"
SESSION_MODEL = STATE_DIR / "session" / "model-state.json"
SESSION_ORCHESTRATOR = STATE_DIR / "session" / "orchestrator-state.json"
SESSION_PLAN_ITERATION = STATE_DIR / "session" / "plan-iteration-state.json"
SESSION_WAVE_POSITION = STATE_DIR / "session" / "wave-position"
SESSION_PLAN_REVIEWED = STATE_DIR / "session" / "plan-reviewed-session"
SESSION_ACTIVE_IDENTITY = STATE_DIR / "session" / "active-identity"
SESSION_OPERATION = STATE_DIR / "session" / "operation-state.json"
SESSION_OPERATION_COUNTER = STATE_DIR / "session" / "operation-counter.json"
SESSION_LAST_ASSISTANT_MSG = STATE_DIR / "session" / "last-assistant-msg.txt"
# --- Features ---
FEATURE_CONTEXT_GATE = STATE_DIR / "features" / "context-gate-state.json"
FEATURE_READ_CACHE = STATE_DIR / "features" / "read-cache.json"
FEATURE_QUALITY_DEBOUNCE = STATE_DIR / "features" / "quality-debounce.json"
FEATURE_KNOWLEDGE_WORK_DEBOUNCE = STATE_DIR / "features" / "knowledge-work-debounce.json"
FEATURE_RECALL_CACHE = STATE_DIR / "features" / "recall-session-cache.json"
FEATURE_DEPLOYMENT_TOKEN = STATE_DIR / "features" / "deployment-token-hash"

# --- Audit ---
AUDIT_CHAIN = STATE_DIR / "audit" / "audit-chain-state.json"
AUDIT_DRIFT = STATE_DIR / "audit" / "drift-state.json"
AUDIT_INSIGHT_STACK = STATE_DIR / "audit" / "insight-stack.json"
AUDIT_INSIGHT_VERIFIER = STATE_DIR / "audit" / "insight-verifier-state.json"
AUDIT_SIGNING_KEY = STATE_DIR / "audit" / "audit-signing-key"

# --- Approvals ---
APPROVAL_DESTRUCTIVE = STATE_DIR / "approvals" / "destructive-approval.json"

# --- Integrations ---
INTEGRATION_GRIP_UI = STATE_DIR / "integrations" / "grip-ui-state.json"
INTEGRATION_KONO_CAPTURE = STATE_DIR / "integrations" / "kono-auto-capture-cache.json"
INTEGRATION_KONO_COUNTERS = STATE_DIR / "integrations" / "kono-session-counters.json"

# --- Memory ---
MEMORY_AUTO = STATE_DIR / "memory" / "auto-memory-state.json"

# --- Logs ---
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"


def wave_position_path(claude_dir: Path = CLAUDE_DIR) -> Path:
    """Resolve the active wave-position file, preferring the migrated state path.

    ``lib/wave_state.py`` writes ``state/session/wave-position``
    (``SESSION_WAVE_POSITION``); the root ``.wave-position`` is the pre-migration
    name, read only while it still exists (GRIP#8470). One fallback, shared by
    ``lib/statusline_core.py`` and the shell readers (``grip_wave_position_path``
    in ``lib/grip-paths.sh``), so the two spellings cannot drift apart.
    """
    migrated = claude_dir / "state" / "session" / "wave-position"
    return migrated if migrated.exists() else claude_dir / ".wave-position"


def migrate_if_needed(new_path: Path, old_path: Path) -> None:
    """One-time migration: copy old dotfile to new state/ location.

    Called at runtime when code first accesses a state file.
    Safe to call multiple times (idempotent).
    """
    if old_path.exists() and not new_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(old_path, new_path)


# Legacy dotfile -> new state path mapping for auto-migration
_MIGRATIONS = {
    ".rsi-stop-signal": RSI_STOP_SIGNAL,
    ".rsi-last-collect.json": RSI_LAST_COLLECT,
    ".rsi-active.lock": RSI_ACTIVE_LOCK,
    ".rsi-checkpoint.json": RSI_CHECKPOINT,
    ".rsi-convergence.json": RSI_CONVERGENCE,
    ".rsi-delegate-state.json": RSI_DELEGATE,
    ".rsi-schedule.json": RSI_SCHEDULE,
    ".rsi-sync-state.json": RSI_SYNC,
    ".rsi-slack-queue.json": RSI_SLACK_QUEUE,
    ".auto-converge-state.json": RSI_AUTO_CONVERGE,
    ".no-auto-improve": RSI_NO_AUTO_IMPROVE,
    ".autonomous-state.json": SESSION_AUTONOMOUS,
    ".model-state.json": SESSION_MODEL,
    ".orchestrator-state.json": SESSION_ORCHESTRATOR,
    ".plan-iteration-state.json": SESSION_PLAN_ITERATION,
    ".wave-position": SESSION_WAVE_POSITION,
    ".context-gate-state.json": FEATURE_CONTEXT_GATE,
    ".read-cache.json": FEATURE_READ_CACHE,
    ".quality-debounce.json": FEATURE_QUALITY_DEBOUNCE,
    ".recall-session-cache.json": FEATURE_RECALL_CACHE,
    ".drift-state.json": AUDIT_DRIFT,
    ".insight-stack.json": AUDIT_INSIGHT_STACK,
    ".insight-verifier-state.json": AUDIT_INSIGHT_VERIFIER,
    ".destructive-approval.json": APPROVAL_DESTRUCTIVE,
    ".grip-ui-state.json": INTEGRATION_GRIP_UI,
    ".kono-auto-capture-cache.json": INTEGRATION_KONO_CAPTURE,
    ".kono-session-counters.json": INTEGRATION_KONO_COUNTERS,
    ".auto-memory-state.json": MEMORY_AUTO,
    ".evo-last-run": RSI_EVO_LAST_RUN,
    ".grip-heartbeat-last": RSI_HEARTBEAT_LAST,
    ".operation-counter.json": SESSION_OPERATION_COUNTER,
    ".last-assistant-msg.txt": SESSION_LAST_ASSISTANT_MSG,
    ".improvement-queue.json": RSI_IMPROVEMENT_QUEUE,
    ".knowledge-work-debounce.json": FEATURE_KNOWLEDGE_WORK_DEBOUNCE,
}

# The same mapping keyed by the new file's place under state/, so state_file
# can find the old name for a path under any install root.
_LEGACY_BY_STATE_PATH = {new.relative_to(STATE_DIR): old for old, new in _MIGRATIONS.items()}


def state_file(new_path: Path, claude_dir: Path = CLAUDE_DIR) -> Path:
    """Return ``new_path`` ready to read and write (April audit task D5).

    Its folder is created if missing, but only inside an install root that
    exists: like grip_ensure_log_dir in lib/grip-paths.sh, this never
    creates the root itself, because a fresh install refuses one that is
    already there. If the file has a pre-migration root dotfile in
    ``_MIGRATIONS`` and only that old file exists, the old value is copied
    over first, so a caller that moves to the state/ path still reads what
    was written at the old one. The old file stays in place, so a process
    still running older code keeps working until it is updated.
    """
    try:
        old_name = _LEGACY_BY_STATE_PATH.get(new_path.relative_to(claude_dir / "state"))
    except ValueError:
        old_name = None
    if old_name is not None:
        migrate_if_needed(new_path, claude_dir / old_name)
    if claude_dir.is_dir():
        new_path.parent.mkdir(parents=True, exist_ok=True)
    return new_path


def run_migrations() -> int:
    """Run all pending file migrations. Returns count of files migrated."""
    count = 0
    for old_name, new_path in _MIGRATIONS.items():
        old_path = CLAUDE_DIR / old_name
        if old_path.exists() and not new_path.exists():
            migrate_if_needed(new_path, old_path)
            count += 1
    return count
