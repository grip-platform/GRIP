#!/usr/bin/env python3
"""
Model State Manager - Tracks active model and context window for ACE.

Manages ~/.claude/.model-state.json which stores the current model configuration.
Used by context-gate-hook.py to determine dynamic thresholds and by
statusline-command.sh to display current model state.

Commands:
    extend [session_id]  - Switch to opus[1m] / 1M context (legacy, opus now defaults to 1M)
    revert [session_id]  - Switch back to opus / 1M context (default); hands
                           context_window/extended back to tier control
    status               - Print current model state
    get-context-max      - Print context window size (integer)
    reset                - Reset to defaults (opus/1M)
    sync-from-tier       - Sync context_window/extended to the resolved
                           GRIP plan-tier's envelope (see sync_from_tier())

Note: 1M context is GA for Opus 4.7 (Apr 2026), Opus 4.6 and Sonnet 4.6 (Mar 2026) at standard pricing.

The state file also carries `set_by`, recording who last wrote
context_window/extended: "tier" (sync_from_tier, the SessionStart default),
"extend" (extend()/extend_fast(), an explicit /extend-context -- never
downgraded by a later tier sync), or "revert" (revert(), handing control
back to the tier so the next sync applies).

Version: 1.2.0 (1M context GA — ACE legacy)
"""

import json
import sys
from datetime import datetime, timezone

# Use centralized state manager (GRASP: Pure Fabrication).
#
# Dual-context import (mirrors lib/gates/context_gate.py): this module runs
# under two different sys.path shapes. A caller with the repo root on
# sys.path (tests, lib.* importers) resolves `lib.state_manager` directly.
# hooks/grip-tier-autodetect-hook.py and hooks/session-start.sh's
# `model_state_manager.py reset` invocation run it with ONLY ~/.claude/lib on
# sys.path — grip-python.sh execs the script by its own path, which puts
# that script's directory, not the repo root, at sys.path[0]. There
# `lib.state_manager` does not resolve (ModuleNotFoundError: No module named
# 'lib', confirmed by running it) and the bare name does. Without this
# fallback every real invocation from those two call sites raised at import
# time — session-start.sh's `reset` call swallows it with
# `2>/dev/null || true`, so it has been silently failing every session start.
try:
    from lib.state_manager import get_state_manager
except ImportError:  # imported with ~/.claude/lib (not ~/.claude) on path
    from state_manager import get_state_manager

STATE_FILE = get_state_manager().path("model-state")

# Model profiles
MODEL_PROFILES = {
    "opus": {"context_window": 1000000, "alias": "opus"},
    "opus[1m]": {"context_window": 1000000, "alias": "opus[1m]"},
    "opus-fast": {"context_window": 1000000, "alias": "opus-fast"},  # Opus 4.7 fast mode (CC 2.1.50+)
    "sonnet": {"context_window": 1000000, "alias": "sonnet"},  # Sonnet 4.6 1M GA (March 2026)
    "fable": {"context_window": 1000000, "alias": "fable"},  # Fable 5 (Max-plan 1M window)
    "fable[1m]": {"context_window": 1000000, "alias": "fable[1m]"},  # Fable 5 1M REPL selector
}

DEFAULT_STATE = {
    "active_model": "opus",
    "context_window": 1000000,
    "extended": False,
    "switch_history": [],
}
# NOTE: active_model is a THRESHOLD TRACKER only — it tells context-gate-hook.py
# which context limit to enforce (1M default). It does NOT control which model
# Claude Code actually runs. The real model is set in settings.json ("opus[1m]")
# and can only be changed mid-session via the /model slash command.
# opus-fast = Opus 4.7 fast mode with 1M context (CC 2.1.50+, toggled via /fast).


def load_state() -> dict:
    """Load model state from file, returning defaults if missing."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_STATE.copy()


def save_state(state: dict):
    """Save model state to file."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    STATE_FILE.chmod(0o600)


def extend(session_id: str = ""):
    """Switch to opus[1m] with 1M context window.

    Requires "extra usage" enabled on your Claude Code account:
    https://support.claude.com/en/articles/12429409-extra-usage-for-paid-claude-plans
    """
    state = load_state()
    previous = state["active_model"]

    state["active_model"] = "opus[1m]"
    state["context_window"] = 1000000
    state["extended"] = True
    state["set_by"] = "extend"
    state["switch_history"].append({
        "from": previous,
        "to": "opus[1m]",
        "action": "extend",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
    })

    # Keep last 20 history entries
    state["switch_history"] = state["switch_history"][-20:]
    save_state(state)
    print(f"Extended: {previous} -> opus[1m] (1M context)")
    # Emit structured signal — context-gate-hook and the /extend-context skill
    # detect this to auto-execute the model switch in the current session.
    print("[ACE:SWITCH] model=opus[1m] context=1000000")


def extend_fast(session_id: str = ""):
    """Switch to opus-fast with 1M context window.

    Opus 4.7 fast mode (CC 2.1.50+, toggled via /fast) uses the same model
    with faster output and full 1M context. Thresholds match opus[1m].
    """
    state = load_state()
    previous = state["active_model"]

    state["active_model"] = "opus-fast"
    state["context_window"] = 1000000
    state["extended"] = True
    state["set_by"] = "extend"
    state["switch_history"].append({
        "from": previous,
        "to": "opus-fast",
        "action": "extend-fast",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
    })

    state["switch_history"] = state["switch_history"][-20:]
    save_state(state)
    print(f"Extended: {previous} -> opus-fast (1M context, fast mode)")
    print("[ACE:SWITCH] model=opus-fast context=1000000")


def revert(session_id: str = ""):
    """Switch back to opus with 1M context window (default since Opus 4.7)."""
    state = load_state()
    previous = state["active_model"]

    state["active_model"] = "opus"
    state["context_window"] = 1000000
    state["extended"] = False
    state["set_by"] = "revert"
    state["switch_history"].append({
        "from": previous,
        "to": "opus",
        "action": "revert",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
    })

    state["switch_history"] = state["switch_history"][-20:]
    save_state(state)
    print(f"Reverted: {previous} -> opus (1M context)")
    print("[ACE:SWITCH] model=opus context=1000000")


def sync_from_tier(tier=None) -> dict:
    """Sync context_window/extended to the resolved GRIP plan-tier's envelope.

    Called at SessionStart (hooks/grip-tier-autodetect-hook.py, right after
    grip_tier.autodetect_and_persist()) so the context gate — which reads
    this file's context_window/extended at IMPORT time, before any tier
    logic runs (lib/gates/context_gate.py get_context_limits()) — sees the
    tier's real ceiling from this session's first tool call, instead of
    DEFAULT_STATE's 1M assumption. Without this, a Pro session's gate ran
    with a 1M budget until something else happened to rewrite the file.

    tier resolves through grip_tier.envelope(), which falls through to
    grip_tier.resolve() (env > config > auto-detect) when tier is None —
    never grip_tier.detect_tier() directly, so this never asserts an
    entitlement the resolver itself would not assert.

    A state last set by extend()/extend_fast() (set_by == "extend") is left
    untouched: an explicit /extend-context is never downgraded by a tier
    sync. revert() clears that marker, handing control back to the tier.
    """
    state = load_state()
    if state.get("set_by") == "extend":
        return state

    try:
        from lib.grip_tier import envelope  # repo root on sys.path
    except ImportError:
        from grip_tier import envelope  # ~/.claude/lib only on sys.path (hooks)

    window = envelope(tier).get("assumed_window", 1000000)
    state["context_window"] = window
    state["extended"] = window >= 1000000
    state["set_by"] = "tier"
    save_state(state)
    return state


def status():
    """Print current model state."""
    state = load_state()
    print(json.dumps(state, indent=2))


def get_context_max():
    """Print context window integer for hook consumption."""
    state = load_state()
    print(state.get("context_window", 1000000))


def reset():
    """Reset to default state (opus/1M)."""
    save_state(DEFAULT_STATE.copy())
    print("Reset to defaults: opus / 1M")


def main():
    if len(sys.argv) < 2:
        print("Usage: model-state-manager.py <extend|extend-fast|revert|status|get-context-max|reset|sync-from-tier> [session_id]")
        sys.exit(1)

    command = sys.argv[1]
    session_id = sys.argv[2] if len(sys.argv) > 2 else ""

    commands = {
        "extend": lambda: extend(session_id),
        "extend-fast": lambda: extend_fast(session_id),
        "revert": lambda: revert(session_id),
        "status": status,
        "get-context-max": get_context_max,
        "reset": reset,
        # Silent by design (no print) -- hooks/session-start.sh calls this
        # right after `reset`, every session, and a confirmation line here
        # would leak into the SessionStart banner on every launch.
        "sync-from-tier": sync_from_tier,
    }

    if command not in commands:
        print(f"Unknown command: {command}")
        print(f"Valid: {', '.join(commands.keys())}")
        sys.exit(1)

    commands[command]()


if __name__ == "__main__":
    main()
