#!/usr/bin/env python3
"""
Unified Enforcement Hook - PARAMOUNT Gates
==========================================

PreToolUse hook that BLOCKS tool execution for multiple enforcement rules.
Provides ACTUAL enforcement via permissionDecision: "deny".

Gates (priority order):
C. Remote Execution Verification (Priority 1) - Verify execution context
0. Always Allow - Critical tools (AskUserQuestion, TodoWrite, TaskCreate, etc.)
1. Dependency Guardian - Block reads from node_modules, venv, dist, etc.
K. Cross-Org Read Protection - Block reads from other org directories
L. Slack Workspace Routing - Block wrong workspace for active org
M. Guest RSI Block - Block RSI/persistence in guest sessions
2. Destructive Git - Block force push, hard reset without explicit approval
2a. Git Push Gate - Block force push and protected branch push
3. Secrets Detection - Block commits containing sensitive files
A. Environment Variable Protection (Priority 10) - Block reading sensitive env vars
B. Database Exposure Prevention (Priority 11) - Block database dumps
4. Read Cache - Block redundant file reads (>2x per session)
5. Bash Tool Substitution - Block grep/find/cat misuse
6. Bash Linting - Block dangerous bash patterns
7. Temp Script Prevention - Block temporary script creation
D. Network Binding Protection (Priority 12) - Block 0.0.0.0 binding without auth
E. Deploy Gate - Block deployment commands (vercel, firebase, npm publish, etc.)
F. SSH Blocking Gate (Priority 13) - Block SSH/remote access (all SSH hosts are prod)
G. Steering Protection (Priority 14) - Block dangerous operations triggered via steering
H. Stash Pop Safety - Warn on cross-branch stash pop (silent data loss prevention)
J. MCP Apps Surface - Block grip_ UI tools in CLI (no rendering surface)
R. Restricted Docs - Block reads of docs/restricted/ without V>> identity
P. Plan Review Gate - Block auto-execution of cached preplans after context compaction
P2. Plan Iteration Limit - Warn after 2 plan-mode cycles per session
8. Skill Creation - Soft warning (allow + remind)
Q. Complexity Gate - Warn on O(n^2)+ nested loops in Python (BIG-O principle)

Note: Gate W (WhatsApp Channel Authorization) was removed in security audit Phase 2.
Channel authorization belongs in the gateway layer, not the enforcement hook.

Architecture:
- Reads tool_name and tool_input from hook input
- Applies enforcement rules in priority order
- Returns permissionDecision: "deny" to BLOCK execution

Version: 4.0.0 (Gen 305+ - GRIP Security Lockdown Gates)
"""

import json
import os
import re
import sys
import traceback
import time
from dataclasses import dataclass, field
from pathlib import Path

from datetime import datetime, timezone

# Ensure hooks directory and lib/ are in path for local imports.
# Code is imported from the tree this hook lives in, never from ~/.claude:
# where ~/.claude is a stale copy rather than a link to the clone (a Windows
# teammate's layout, where Python reads the home folder from USERPROFILE),
# importing from it crashed this gate before it could check anything
# (fleet report 2026-10-02, B-2). Data paths are unchanged.
_HOOK_TREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(_HOOK_TREE / "lib"))
sys.path.insert(0, str(_HOOK_TREE))
from file_lock import FileLock

# API Audit logging (REDR compliance)
try:
    from api_audit import log_tool_call
    AUDIT_ENABLED = True
except ImportError:
    AUDIT_ENABLED = False
    def log_tool_call(*args, **kwargs):
        pass

# Locked append for concurrent-safe JSONL writes
try:
    sys.path.insert(0, str(_HOOK_TREE / "lib"))
    from locked_append import locked_append as _locked_append
except ImportError:
    def _locked_append(filepath, line):
        with open(filepath, "a") as f:
            f.write(line if line.endswith("\n") else line + "\n")

# =========================================================================
# EXTRACTED MODULES (#933) - Context gate + approval manager
# =========================================================================
from gates.context_gate import (
    get_context_limits,
    estimate_tokens_from_jsonl,
    get_state,
    save_state,
    _get_context_gate_summary,
    check_soft_checkpoints as _check_soft_checkpoints_raw,
    CONTEXT_MAX,
    CONTEXT_IS_EXTENDED,
    CONTEXT_WARNING,
    CONTEXT_CRITICAL,
    SOFT_CHECKPOINTS,
    STATE_FILE,
)
from gates.approval_manager import (
    _get_instance_uuid,
    _load_approvals,
    _save_approvals,
    _locked_approval_operation,
    _is_approval_valid,
    check_destructive_approval,
    grant_destructive_approval,
    clear_destructive_approval,
    _normalize_action_type as _approval_normalize_action_type,
    actions_requested_by_question,
)
from gates.bash_orchestrator import run_bash_gates
from gates.context_orchestrator import run_context_gate
from gates.read_cache_gate import (
    load_read_cache,
    save_read_cache,
    check_read_cache as _check_read_cache_raw,
    _reset_read_cache_entry,
    configure_logging as _configure_read_cache_logging,
)

# State files for tracking
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR_OVERRIDE", str(Path.home() / ".claude")))
GRIP_UI_STATE_FILE = CLAUDE_DIR / "state" / "integrations" / "grip-ui-state.json"

# Gate modulation: Cube convergence -> gate thresholds (lazy, cached)
_gate_thresholds_cache = None

def _get_gate_thresholds():
    """Lazy-load gate thresholds from Cube convergence state.

    Caches for session lifetime. Falls back to DEFAULT on any error.
    """
    global _gate_thresholds_cache
    if _gate_thresholds_cache is not None:
        return _gate_thresholds_cache
    try:
        from lib.gate_modulator import get_gate_thresholds
        _gate_thresholds_cache = get_gate_thresholds()
    except Exception:
        # Fallback: DEFAULT thresholds (current behaviour unchanged)
        _gate_thresholds_cache = type('GateThresholds', (), {
            'read_cache_max': 2,
            'temp_script_action': 'deny',
            'skill_creation_action': 'warn',
            'complexity_ceiling': 'n^2',
        })()
    return _gate_thresholds_cache

@dataclass
class GateContext:
    """Shared context passed to all gate functions in the pipeline."""
    tool_name: str
    tool_input: dict
    session_id: str
    transcript_path: str
    target: str
    advisories: list = field(default_factory=list)
    agent_id: str = ""
    agent_type: str = ""
    cwd: str = ""  # the session's working directory, from the hook payload


# APPROVAL_FILE: now in gates.approval_manager
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"
GATE_METRICS_FILE = CLAUDE_DIR / "data" / "gate-metrics.jsonl"
RSI_STOP_SIGNAL = CLAUDE_DIR / "state" / "rsi" / "rsi-stop-signal"
PLAN_ITERATION_FILE = CLAUDE_DIR / "state" / "session" / "plan-iteration-state.json"
MAX_PLAN_ITERATIONS = 2  # Warn after this many enter/exit cycles per session

# =========================================================================
# EXTRACTED MODULES (#933) - Gate patterns, bash/git/security gates
# =========================================================================
from gates.patterns import (
    SAFE_BASH_PREFIXES as _SAFE_BASH_PREFIXES,
    APPROVAL_EXPIRY_SECONDS,
    ACTION_FORCE_PUSH, ACTION_GIT_PUSH, ACTION_CODE_WRITE,
    ACTION_DEPLOY, ACTION_TAILSCALE_SSH,
    ACTION_SUPERSETS, ACTION_ALIASES as _ACTION_ALIASES,
    CODE_WRITE_APPROVAL_KEYWORDS, CODE_WRITE_BATCH_KEYWORDS,
    CODE_EXTENSIONS, MAX_READS_PER_FILE,
    COMPILED_ENV_PATTERNS, COMPILED_DB_PATTERNS,
    REMOTE_MODE_ENABLED, DEPLOYMENT_TOKEN_VALUE as DEPLOYMENT_TOKEN,
    COMPILED_BINDING_PATTERNS, COMPILED_AUTH_PATTERNS,
    STEERING_SOURCE, STEERING_DANGEROUS_PATTERNS,
    BLOCKED_PATHS, DESTRUCTIVE_GIT_PATTERNS,
    SENSITIVE_FILE_PATTERNS, ENV_SAFE_SUFFIXES,
    GIT_PUSH_PATTERN, PROTECTED_BRANCHES,
    GIT_WRITE_OPS as _GIT_WRITE_OPS,
    GIT_LOCK_STALE_THRESHOLD_SECONDS,
    TOOL_SUBSTITUTIONS, BASH_LINT_PATTERNS,
    TEMP_SCRIPT_PATTERNS, TEMP_SCRIPT_EXTENSIONS,
    LONG_STRING_THRESHOLD as _LONG_STRING_THRESHOLD,
    DEPLOY_COMMAND_PATTERNS, SSH_BLOCKED_PATTERNS, SSH_ALLOWED_PATTERNS,
    GRIP_UI_TOOLS, MAX_PLAN_ITERATIONS,
    UNSAFE_BINDING_PATTERNS, AUTH_PATTERNS,
    SENSITIVE_ENV_PATTERNS, DATABASE_DUMP_PATTERNS,
)
from gates.bash_gates import (
    strip_heredocs_and_strings as _strip_heredocs_and_strings,
    check_bash_tools as _check_bash_tools_raw,
    check_bash_lint as _check_bash_lint_raw,
    detect_nested_loops as _detect_nested_loops,
    check_temp_script as _check_temp_script_raw,
)
from gates.git_gates import (
    is_destructive_git,
    contains_sensitive_file,
    is_force_push as _is_force_push,
    is_protected_branch_push as _is_protected_branch_push,
    is_string_literal_context as _is_string_literal_context,
    spawn_approval_reminder as _spawn_approval_reminder,
    check_git_push_gate as _check_git_push_gate_raw,
    check_stash_pop_safety as _check_stash_pop_safety_raw,
    check_stash_drop_learning_data as _check_stash_drop_raw,
    check_infra_test_gate as _check_infra_test_gate_raw,
    check_branch_switch as _check_branch_switch_raw,
    find_git_dir as _find_git_dir,
    get_lock_owner_pid as _get_lock_owner_pid,
    check_git_index_lock as _check_git_index_lock_raw,
)
from gates.security_gates import (
    _match_any,
    check_env_var_exposure as _check_env_var_exposure_raw,
    check_database_exposure as _check_database_exposure_raw,
    verify_remote_execution_context as _verify_remote_raw,
    check_network_binding as _check_network_binding_raw,
    check_deploy_gate as _check_deploy_gate_raw,
    check_ssh_blocking as _check_ssh_blocking_raw,
    check_steering_protection as _check_steering_raw,
    gate_k_cross_org_read as _gate_k_raw,
    gate_l_slack_workspace_routing as _gate_l_raw,
    gate_m_guest_rsi_block as _gate_m_raw,
)

# Module-level gate evaluation context (set in main, read in deny/allow)
_eval_ctx = {}


def is_code_file(path: str | None) -> bool:
    """Check if a file path refers to a code file by extension."""
    if not path:
        return False
    from pathlib import PurePosixPath
    return PurePosixPath(path).suffix.lower() in CODE_EXTENSIONS


# _normalize_action_type: imported from gates.approval_manager
_normalize_action_type = _approval_normalize_action_type


DEPLOYMENT_TOKEN_HASH_FILE = CLAUDE_DIR / "state" / "features" / "deployment-token-hash"

# =========================================================================
# PLAN CACHE HELPERS - fcntl-locked read/write for concurrent session safety
# =========================================================================

def _read_plan_cache_safe(path: Path) -> dict | None:
    """Read plan cache with shared file locking for concurrent access."""
    try:
        with FileLock(path, shared=True, timeout=2):
            return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write_plan_cache_safe(path: Path, data: dict):
    """Write plan cache with exclusive file locking for concurrent access."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path, timeout=5):
        path.write_text(json.dumps(data))


# =========================================================================
# AUTONOMOUS MODE - Imported from hooks/autonomous_mode.py (extracted TODO #10)
# =========================================================================
# Import path: autonomous_mode.py lives in the same hooks/ directory as this file.
# The try/except fallback adds hooks/ to sys.path if direct import fails.
# See autonomous_mode.py for full security model documentation.
AUTONOMOUS_MODE = os.environ.get("GRIP_AUTONOMOUS", "")

# Import autonomous mode functions from extracted module
try:
    from autonomous_mode import (
        AUTONOMOUS_CONSENT_PHRASE,
        AUTONOMOUS_GATES,
        _check_autonomous_consent,
        _ensure_autonomous_secret,
        _load_autonomous_state,
        _parse_autonomous_value,
        _save_autonomous_state,
        _validate_autonomous_key,
        get_autonomous_level as _get_autonomous_level_raw,
        is_autonomous_bypass as _is_autonomous_bypass_raw,
    )
except ImportError:
    # Fallback: add hooks dir to sys.path
    sys.path.insert(0, str(Path(__file__).parent))
    from autonomous_mode import (
        AUTONOMOUS_CONSENT_PHRASE,
        AUTONOMOUS_GATES,
        _check_autonomous_consent,
        _ensure_autonomous_secret,
        _load_autonomous_state,
        _parse_autonomous_value,
        _save_autonomous_state,
        _validate_autonomous_key,
        get_autonomous_level as _get_autonomous_level_raw,
        is_autonomous_bypass as _is_autonomous_bypass_raw,
    )


def get_autonomous_level() -> int:
    """Wrapper that adds logging for the enforcement hook."""
    level = _get_autonomous_level_raw(AUTONOMOUS_MODE)
    if AUTONOMOUS_MODE and level == 0:
        provided_key, _ = _parse_autonomous_value(AUTONOMOUS_MODE)
        if not _validate_autonomous_key(provided_key):
            log_hook("WARN", "GRIP_AUTONOMOUS set but invalid key")
        elif not _check_autonomous_consent():
            log_hook("WARN", "GRIP_AUTONOMOUS valid key but no consent")
    return level


def is_autonomous_bypass(gate: str) -> bool:
    """Wrapper that adds logging and metrics for the enforcement hook.

    Two independent authorisations are honoured (issue #4370): the keyed
    GRIP_AUTONOMOUS env system, and the operator-activated yolo marker
    (lib/gates/yolo_mode), whose per-preset sets scope which HALTING gates
    it may bypass — quality and safety gates are not in any preset. The
    deny texts have promised the yolo lever since these gates shipped;
    this wiring makes that promise true.
    """
    level = get_autonomous_level()
    if level > 0 and _is_autonomous_bypass_raw(gate, level):
        log_hook("AUTONOMOUS", f"Bypassed gate '{gate}' (level={level})")
        log_gate_metric(gate, "autonomous_bypass")
        return True
    try:
        from gates.yolo_mode import is_yolo_bypass
    except ImportError:
        return False
    if is_yolo_bypass(gate):
        log_hook("YOLO", f"Bypassed gate '{gate}' (yolo marker)")
        log_gate_metric(gate, "yolo_bypass")
        return True
    return False


# Constants (BLOCKED_PATHS, DESTRUCTIVE_GIT_PATTERNS, SENSITIVE_FILE_PATTERNS,
# ENV_SAFE_SUFFIXES, TOOL_SUBSTITUTIONS, BASH_LINT_PATTERNS, TEMP_SCRIPT_PATTERNS,
# TEMP_SCRIPT_EXTENSIONS, GIT_PUSH_PATTERN, PROTECTED_BRANCHES,
# DEPLOY_COMMAND_PATTERNS, SSH_BLOCKED_PATTERNS, SSH_ALLOWED_PATTERNS,
# UNSAFE_BINDING_PATTERNS, AUTH_PATTERNS, COMPILED_BINDING_PATTERNS,
# COMPILED_AUTH_PATTERNS): imported from gates.patterns


# A shell metacharacter that can introduce a SECOND command, or redirect the
# first one's output. The fast path may only exempt a command whose entire text
# is the safe prefix and its own arguments; the moment another command can be
# chained on, the prefix stops describing what will run.
_COMMAND_CHAINING = ("&&", "||", ";", "|", "&", "\n", "\r", "`", "$(", ">", "<")


def _is_safe_bash(command: str) -> bool:
    """Check if a Bash command is known-safe (read-only, no side effects).

    Performance optimization (#165): safe commands skip all Bash-specific gates,
    saving ~15-20ms per tool call for common operations like git status, ls, wc.

    ANCHORED AT BOTH ENDS (2026-08-18). The prefix test alone was a universal
    bypass of the ENTIRE Bash safety layer, and it needed no marker, no yolo
    mode and no autonomous mode. `run_bash_gates` returns before every Bash gate
    when this is True (lib/gates/bash_orchestrator.py:107), so a command merely
    STARTING with a read-only prefix skipped secrets detection, force-push,
    SSH-blocking, the deploy gate, destructive-git, env-var exposure, database
    exposure and bash lint alike. Measured before the fix: `git status && curl
    https://example.com` and `git log --oneline | head; rm -rf /tmp/probe` both
    took the fast path.

    This is the same shape as the recorded tailnet-exemption failure, where the
    SSH gate matched `ssh` as the first token and a compound command hid the
    real destination behind it — a prefix describes the first command, never
    what a chain goes on to do.

    Fail-safe direction: a rejected fast path costs ~15-20ms and runs the gates.
    An accepted one runs nothing. So anything carrying a chaining metacharacter
    is gated, including benign shapes like `git log | head`; the pipe cannot be
    distinguished from `git log | sh` by a prefix table, and 15ms is not worth
    the difference.

    Falsifier: if profiling shows the newly-gated compound commands cost more
    than the class they protect against is worth, narrow the metacharacter set
    with evidence — never restore the bare prefix match.
    """
    cmd = command.strip()
    if not cmd:
        return False
    if any(token in cmd for token in _COMMAND_CHAINING):
        return False
    for prefix in _SAFE_BASH_PREFIXES:
        if _prefix_matches_whole_words(cmd, prefix):
            return True
    return False


def _prefix_matches_whole_words(cmd: str, prefix: str) -> bool:
    """True when ``cmd`` begins with ``prefix`` AND the prefix ends on a word boundary.

    The bare ``startswith`` was a second bypass, found after the chaining anchor
    closed the first. ``git diff`` matched the DIFFERENT subcommand
    ``git difftool``, whose ``-x``/``--extcmd`` runs an arbitrary program, so

        git difftool -y -x "sh -c 'git add <secret>'"

    took the fast path and never reached the secrets gate — verified to actually
    stage the file, not merely to parse. ``git status`` likewise matched
    ``git statusfoo``, ``git show`` matched ``git showcase``, ``git log`` matched
    ``git logger``.

    Requiring the next character to be a space (or nothing) means a prefix names
    a whole command word rather than a spelling. Falsifier: a legitimate safe
    command whose real name merely EXTENDS a listed prefix would now be gated —
    add it to SAFE_BASH_PREFIXES in its own right rather than relaxing this.
    """
    stripped = prefix.rstrip()
    if cmd == stripped:
        return True
    return cmd.startswith(stripped) and cmd[len(stripped):len(stripped) + 1] == " "


def ask(reason: str) -> dict:
    """Return an ASK response: the harness prompts the operator instead of blocking.

    V>>--<<V 2026-09-07: "rules always ask; if there is a timeout assume yes."
    The ``ASK:`` routing prefix is stripped before display. Metric logged as "ask"
    so the gate's fire rate is measurable separately from denies.
    """
    from lib.gates.security_gates import ASK_PREFIX
    shown = reason[len(ASK_PREFIX):] if reason.startswith(ASK_PREFIX) else reason
    gate_match = re.search(r'\[GUARDRAIL: ([^\]\-]+)', shown)
    if gate_match:
        log_gate_metric(gate_match.group(1).strip().lower().replace(' ', '-'), "ask")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "ask",
                                   "permissionDecisionReason": shown}}


def deny(reason: str, *, log_info: dict | None = None) -> dict:
    """Return a deny response, optionally logging to audit trail.

    Args:
        reason: Deny reason (shown to user).
        log_info: Optional audit log info with keys: session_id, tool_name,
                  tool_input, transcript_path. When provided, logs to audit trail.
    """
    # Auto-log gate metric by parsing gate name from reason
    gate_match = re.search(r'\[GUARDRAIL: ([^\]\-]+)', reason)
    if gate_match:
        gate_name = gate_match.group(1).strip().lower().replace(' ', '-')
        log_gate_metric(gate_name, "deny")
    if log_info and AUDIT_ENABLED:
        log_tool_call(
            log_info.get("session_id", ""),
            log_info.get("tool_name", ""),
            log_info.get("tool_input", {}),
            "deny",
            log_info.get("transcript_path", ""),
        )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason
        }
    }


def deny_and_log(reason: str, session_id: str, tool_name: str, tool_input: dict, transcript_path: str = "") -> dict:
    """Return a deny response and log to audit trail (convenience wrapper)."""
    return deny(reason, log_info={
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "transcript_path": transcript_path,
    })


def ctx_deny(ctx, reason: str) -> dict:
    """Deny with full context logging (DRY helper for gate functions)."""
    return deny_and_log(reason, ctx.session_id, ctx.tool_name, ctx.tool_input, ctx.transcript_path)


def allow(system_message: str = None, additional_context: str = None) -> dict:
    """Return an allow response, optionally with a system message and context."""
    hso = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
    }
    if additional_context:
        hso["additionalContext"] = additional_context
    response = {"hookSpecificOutput": hso}
    if system_message:
        response["systemMessage"] = system_message
    return response


# _match_any: imported from gates.security_gates


# _reset_read_cache_entry: imported from gates.read_cache_gate


def _check_and_consume_approval(action_type: str, command: str = "") -> bool:
    """Check if destructive action is pre-approved. Consume the approval if found."""
    if is_autonomous_bypass(action_type):
        return True
    if check_destructive_approval(action_type):
        clear_destructive_approval(action_type)
        return True
    return False


# _get_instance_uuid, _load_approvals, _save_approvals, _locked_approval_operation,
# _is_approval_valid, check_destructive_approval, grant_destructive_approval,
# clear_destructive_approval: imported from gates.approval_manager


def is_blocked_path(path: str) -> str | None:
    """Check if path contains a blocked directory. Returns the blocked component or None."""
    if not path:
        return None

    path_lower = path.lower()
    path_parts = Path(path).parts

    # Allow task output files in tmp directories
    if "/tmp/" in path_lower and path.endswith(".output"):
        return None

    parts_lower = {p.lower() for p in path_parts}  # O(1) lookup instead of nested loop
    for blocked in BLOCKED_PATHS:
        blocked_lower = blocked.lower()
        if blocked_lower in parts_lower:
            return blocked
        # Also check substring for patterns like .git/objects
        # But skip ".output" substring check (handled by exact match above)
        # Require path separator boundaries to avoid false positives
        # (e.g. "dist" matching "distributed-roaming-emerson.md")
        if blocked_lower != ".output" and blocked_lower in path_lower:
            # Verify it's a path component, not a substring of a filename
            boundary_pattern = r'(?:^|/)' + re.escape(blocked_lower) + r'(?:/|$)'
            if re.search(boundary_pattern, path_lower):
                return blocked

    return None


# is_destructive_git, contains_sensitive_file: imported from gates.git_gates


def log_hook(level: str, message: str):
    """Log to hook log file (locked for concurrent session safety)."""
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _locked_append(str(HOOK_LOG), f"{timestamp} [UNIFIED-HOOK][{level}] {message}")
    except Exception:
        pass  # Don't fail on logging errors


# Wire up logging for extracted modules (#933)
from gates.context_gate import configure_logging as _configure_context_gate_logging
from gates.approval_manager import configure_logging as _configure_approval_logging
_configure_context_gate_logging(log_hook)
_configure_approval_logging(log_hook)
_configure_read_cache_logging(log_hook)


def log_gate_metric(gate: str, decision: str, target: str = ""):
    """Append structured gate metric to JSONL file."""
    try:
        GATE_METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "gate": gate,
            "decision": decision,
            "tool": _eval_ctx.get("tool_name", ""),
            "target": (target or _eval_ctx.get("target", ""))[:200],
            "session_id": _eval_ctx.get("session_id", "")[:16]
        }
        _locked_append(str(GATE_METRICS_FILE), json.dumps(entry))
    except Exception:
        pass


# GATES A, B, C, D: delegated to gates.security_gates (wrappers removed Phase 2)


# GATE 4: Read Cache — load_read_cache, save_read_cache, check_read_cache,
# _reset_read_cache_entry: imported from gates.read_cache_gate


# GATES 5, 6, 7: delegated to gates.bash_gates (wrappers removed Phase 2)


# GATE 2a: delegated to gates.git_gates (wrapper removed Phase 2)


# GATES E, F, G, H, I: delegated to gates.security_gates / gates.git_gates
# (wrappers removed Phase 2)


# =========================================================================
# GATE S: Sequential Rendering - Warn on back-to-back grip_ UI tool calls
# (GRIP_UI_TOOLS imported from gates.patterns)
# =========================================================================


def is_grip_ui_tool(tool_name: str) -> bool:
    """Check if tool_name is a GRIP UI tool (handles MCP-prefixed names)."""
    # MCP tools come as mcp__<server>__<tool_name>
    name = tool_name.split("__")[-1] if "__" in tool_name else tool_name
    return name in GRIP_UI_TOOLS


def check_plan_iteration_limit(tool_name: str, session_id: str) -> dict | None:
    """Gate P2: Warn when plan-mode cycles exceed MAX_PLAN_ITERATIONS.

    Tracks EnterPlanMode/ExitPlanMode calls per session. After 2 cycles,
    injects a warning to check in with the user instead of looping.
    """
    if tool_name not in ("EnterPlanMode", "ExitPlanMode"):
        return None

    try:
        state = {}
        if PLAN_ITERATION_FILE.exists():
            state = json.loads(PLAN_ITERATION_FILE.read_text())

        # Reset counter if session changed
        if state.get("session_id") != session_id:
            state = {"session_id": session_id, "enter_count": 0, "exit_count": 0}

        if tool_name == "EnterPlanMode":
            state["enter_count"] = state.get("enter_count", 0) + 1
        else:
            state["exit_count"] = state.get("exit_count", 0) + 1

        PLAN_ITERATION_FILE.write_text(json.dumps(state))

        cycles = min(state.get("enter_count", 0), state.get("exit_count", 0))
        if cycles >= MAX_PLAN_ITERATIONS:
            log_hook("WARN", f"Plan iteration limit: {cycles} cycles in session {session_id[:8]}")
            log_gate_metric("plan-iteration", "warn", f"{cycles} cycles")
            return deny(
                f"[GUARDRAIL: PLAN ITERATION] {cycles} plan-mode cycles detected in this session. "
                f"This may indicate a planning loop — check with the user before continuing."
            )
    except (json.JSONDecodeError, OSError) as e:
        log_hook("ERROR", f"Plan iteration check failed: {e}")

    return None


def _check_rsi_stop_signal():
    """Check for user-requested RSI stop signal."""
    if not RSI_STOP_SIGNAL.exists():
        return None
    try:
        signal = json.loads(RSI_STOP_SIGNAL.read_text())
    except (ValueError, OSError, RecursionError):
        # ValueError covers both JSONDecodeError and UnicodeDecodeError (bytes
        # that are not UTF-8); RecursionError is JSON nested too deep to parse.
        signal = None
    # The file's existence is the stop request. Valid JSON that is not an object
    # (echo 1 > ...) used to crash this gate, and the crash reply invited editing
    # the gate itself; it now halts like any other unreadable signal.
    if isinstance(signal, dict):
        reason = signal.get("reason", "User requested stop")
        by = signal.get("requested_by", "unknown")
    else:
        reason, by = "Stop signal file exists", "unknown"
    log_hook("BLOCK", f"RSI stop signal from {by}: {reason}")
    log_gate_metric("rsi-stop", "deny", reason)
    return deny(
        f"[RSI STOP] Halt requested: {reason}. "
        f"Save all state, commit work, and notify user. "
        f"Do NOT continue with any further operations."
    )


def _check_convergence_stall():
    """Check for convergence score stall (3+ scores in 70-84 range)."""
    conv_file = CLAUDE_DIR / "state" / "rsi" / "rsi-convergence-state.json"
    try:
        if not conv_file.exists():
            return None
        conv = json.loads(conv_file.read_text())
        stall_count = conv.get("stall_count", 0)
        recent = conv.get("recent_scores", [])
        if stall_count > 0 and len(recent) >= 3:
            last_3 = recent[-3:]
            if all(70 <= s < 85 for s in last_3):
                avg = sum(last_3) / len(last_3)
                log_hook("BLOCK", f"Convergence stall: {stall_count}x, avg={avg:.0f}")
                log_gate_metric("convergence-stall", "deny", f"stall#{stall_count}")
                return deny(
                    f"[GUARDRAIL: CONVERGENCE STALL] RSI loop stalled: "
                    f"{stall_count} stall(s), last 3 scores={last_3}, avg={avg:.0f}. "
                    f"Stop current approach. Escalate to user or try different strategy."
                )
    except (json.JSONDecodeError, OSError):
        pass
    return None


def check_rsi_control_gate() -> dict | None:
    """Gate $: RSI stop signal and convergence stall detection."""
    if not AUTONOMOUS_MODE and not os.environ.get("GRIP_RSI_MODE", ""):
        return None
    return _check_rsi_stop_signal() or _check_convergence_stall()


def check_mcp_apps_surface(tool_name: str) -> dict | None:
    """Gate J: Block GRIP MCP Apps in CLI (no rendering surface).

    MCP Apps (grip_kanban, grip_wizard, etc.) require a rendering surface
    (iframe/webview). Claude Code CLI has no UI rendering — tool calls
    succeed but output is meaningless JSON.

    Set GRIP_SURFACE=web or GRIP_SURFACE=desktop to allow.
    """
    if not is_grip_ui_tool(tool_name):
        return None

    # Valid surfaces: cli (no rendering), web (browser), desktop (Electron/Tauri)
    # Check env first, then fall back to .env file (hook subprocess doesn't inherit shell exports)
    surface = os.environ.get("GRIP_SURFACE")
    if not surface:
        try:
            env_path = Path.home() / ".claude" / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("GRIP_SURFACE="):
                        surface = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except Exception as _e:
            log_hook("DEBUG", f"GRIP_SURFACE .env read failed: {_e}")
    surface = surface or "cli"
    if surface == "cli":
        short = tool_name.split("__")[-1] if "__" in tool_name else tool_name
        log_hook("BLOCK", f"Gate J: {short} requires rendering surface (current: {surface})")
        log_gate_metric("mcp-surface", "deny", short)
        return deny(
            f"[GUARDRAIL: MCP SURFACE] {short} requires a rendering surface. "
            f"Use the Copilot web dashboard at http://localhost:3000 instead, "
            f"or set GRIP_SURFACE=web to enable MCP Apps rendering."
        )
    return None


def check_sequential_rendering(tool_name: str, session_id: str) -> dict | None:
    """Warn if two consecutive grip_ UI tools called without user message."""
    if not is_grip_ui_tool(tool_name):
        # Not a UI tool — clear the last UI call tracking
        _clear_grip_ui_state(session_id)
        return None

    try:
        if GRIP_UI_STATE_FILE.exists():
            state = json.loads(GRIP_UI_STATE_FILE.read_text())
        else:
            state = {}

        last_tool = state.get("last_tool", "")
        last_session = state.get("session_id", "")

        # Update state with current call
        state["last_tool"] = tool_name
        state["session_id"] = session_id
        state["timestamp"] = datetime.now(timezone.utc).isoformat()
        GRIP_UI_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        GRIP_UI_STATE_FILE.write_text(json.dumps(state))

        # Warn if consecutive UI tools in same session
        if last_tool and last_session == session_id and is_grip_ui_tool(last_tool):
            short_last = last_tool.split("__")[-1] if "__" in last_tool else last_tool
            short_current = tool_name.split("__")[-1] if "__" in tool_name else tool_name
            log_hook("WARN", f"Sequential UI: {short_last} -> {short_current}")
            log_gate_metric("sequential-render", "warn", f"{short_last}->{short_current}")
            return deny(
                f"[GUARDRAIL: SEQUENTIAL RENDER] Back-to-back UI tools: {short_last} then {short_current}. "
                f"Each new artifact replaces the previous one — present one at a time."
            )
    except Exception as e:
        log_hook("ERROR", f"Sequential render check failed: {e}")

    return None


def _clear_grip_ui_state(session_id: str):
    """Clear the last UI tool tracking (non-UI tool was called)."""
    try:
        if GRIP_UI_STATE_FILE.exists():
            state = json.loads(GRIP_UI_STATE_FILE.read_text())
            if state.get("session_id") == session_id and state.get("last_tool"):
                state["last_tool"] = ""
                GRIP_UI_STATE_FILE.write_text(json.dumps(state))
    except Exception as _e:
        log_hook("DEBUG", f"UI state clear failed: {_e}")


# estimate_tokens_from_jsonl, get_state, save_state, _get_context_gate_summary,
# check_soft_checkpoints: imported from gates.context_gate


def check_soft_checkpoints(estimated_tokens: int, state: dict, session_id: str) -> dict | None:
    """Wrapper that passes transcript_path and allow() to extracted implementation."""
    return _check_soft_checkpoints_raw(
        estimated_tokens, state, session_id,
        transcript_path=_eval_ctx.get("transcript_path", ""),
        allow_fn=allow,
    )


# GATES K, L, M: delegated to gates.security_gates (wrappers removed Phase 2)

# =========================================================================
# GATE FUNCTIONS — each returns dict to terminate pipeline, None to continue
# =========================================================================

# Always-allow tool set (used by _gate_0_always_allow)
# HITL-essential tools and task-management tools are always permitted through
# Gate 0's fast path. Blocking any of these via other gates leaves the agent
# unable to ask the user for guidance, exit plan mode, or track its own work —
# all of which are orthogonal to the safety concerns the other gates enforce.
ALLOWED_TOOLS = {
    "AskUserQuestion",
    "ExitPlanMode",
    "EnterPlanMode",
    "TodoWrite",
    "TaskCreate",
    "TaskUpdate",
    "TaskGet",
    "TaskList",
}


def _gate_j_mcp_apps(ctx: GateContext) -> dict | None:
    """Gate J: MCP Apps Surface Detection."""
    surface_block = check_mcp_apps_surface(ctx.tool_name)
    if surface_block:
        return surface_block
    return None


def _gate_p2_plan_iteration(ctx: GateContext) -> dict | None:
    """Gate P2: Plan Iteration Limit (advisory — appends to ctx.advisories)."""
    plan_iter_warn = check_plan_iteration_limit(ctx.tool_name, ctx.session_id)
    if plan_iter_warn and plan_iter_warn.get("systemMessage"):
        ctx.advisories.append(plan_iter_warn["systemMessage"])
    return None  # Advisory only, never terminates


def _gate_s_sequential_rendering(ctx: GateContext) -> dict | None:
    """Gate S: Sequential Rendering (advisory — appends to ctx.advisories)."""
    seq_warn = check_sequential_rendering(ctx.tool_name, ctx.session_id)
    if seq_warn and seq_warn.get("systemMessage"):
        ctx.advisories.append(seq_warn["systemMessage"])
    return None  # Advisory only, never terminates


def _gate_c_remote(ctx: GateContext) -> dict | None:
    """Gate C: Remote Execution Verification."""
    _remote_reason = _verify_remote_raw(log_hook=log_hook)
    if _remote_reason:
        return deny(_remote_reason)
    return None


def _gate_0_always_allow(ctx: GateContext) -> dict | None:
    """Gate 0: Always Allow — critical tools pass straight through.

    This gate used to GRANT destructive approvals from the text of an
    AskUserQuestion, during PreToolUse — i.e. when the question was ASKED,
    before any answer existed. An agent whose question merely contained
    "force push" therefore held force_push approval for its full 600s TTL
    whether the operator clicked yes or no; likewise deploy (3600s),
    tailscale_ssh (14400s) and the autonomous-consent phrase. The approval was
    answer-blind, and nothing ever cleared it on a "no" — the sole
    clear_destructive_approval callsite (:441) is consumption-after-use.

    Granting now lives where the answer does: hooks/askuserquestion-approval-hook.py
    (PostToolUse), which reads tool_response["answers"] and grants only on an
    affirmative selection. What remains here is the operator NUDGE, which
    genuinely belongs at ask-time: a pending git-push question fires a desktop
    reminder so the human knows a prompt is waiting. A notification confers no
    privilege.
    """
    if ctx.tool_name not in ALLOWED_TOOLS:
        return None

    if ctx.tool_name == "AskUserQuestion":
        _notify_pending_approval(ctx.tool_input.get("questions", []))

    log_gate_metric("always-allow", "allow")
    return None  # Pass-through: whitelisted tools still flow through full pipeline


def _notify_pending_approval(questions) -> None:
    """Desktop-notify the operator that a git-push approval prompt is waiting.

    Ask-time side effect only. It reuses the one shared question matcher in
    gates.approval_manager so the reminder and the (answer-gated) grant can
    never drift apart.

    Swallows its own errors deliberately. Gate 0 is on ADVISORY_GATES, so an
    escaping exception would be caught by _run_pipeline and surfaced to the
    operator as a gate-failure advisory — a lot of noise for a desktop
    notification that did not fire. The failure is logged instead.
    """
    if not isinstance(questions, list):
        return
    for q in questions:
        if not isinstance(q, dict):
            continue
        requested = actions_requested_by_question(q.get("question", ""))
        if any(action == ACTION_GIT_PUSH for action, _ in requested):
            try:
                _spawn_approval_reminder(
                    "git push",
                    "Git push approval pending — switch to Claude Code to continue",
                )
            except Exception as e:  # noqa: BLE001 — a missed notification is not a gate failure
                log_hook("WARN", f"Approval reminder failed: {e!r}")
            return


def _gate_autonomous_consent(ctx: GateContext) -> dict | None:
    """Autonomous Consent Gate: require explicit consent before autonomous mode."""
    if not AUTONOMOUS_MODE:
        return None
    provided_key, _ = _parse_autonomous_value(AUTONOMOUS_MODE)
    if _validate_autonomous_key(provided_key) and not _check_autonomous_consent():
        a_state = _load_autonomous_state()
        if a_state.get("prompted_session") != ctx.session_id:
            a_state["prompted_session"] = ctx.session_id
            _save_autonomous_state(a_state)
            log_hook("INFO", "Autonomous consent gate: valid key, no consent")
            return deny(
                "[GUARDRAIL: AUTONOMOUS CONSENT] Valid autonomous key detected but "
                "legal consent not yet given. Autonomous mode bypasses safety gates "
                "(push, code-write, deploy) for AFK execution. Blocked until consent "
                "is obtained via AskUserQuestion."
            )
    return None


def _gate_p_plan_review(ctx: GateContext) -> dict | None:
    """Gate P: Plan Review Gate — prevent auto-execution of cached preplans."""
    plan_cache_file = CLAUDE_DIR / "cache" / "last-plan.json"
    plan_data = _read_plan_cache_safe(plan_cache_file) if plan_cache_file.exists() else None
    if plan_data is None:
        return None

    plan_status = plan_data.get("status", "")
    plan_id = plan_data.get("plan_id", "unknown")
    if plan_status != "active":
        return None

    # Auto-dismiss stale plans (older than 7 days)
    cached_at = plan_data.get("cached_at", "")
    if cached_at:
        try:
            plan_time = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - plan_time).days
            if age_days > 7:
                plan_data["status"] = "stale"
                import json
                plan_cache_file.write_text(json.dumps(plan_data, indent=2) + "\n")
                log_hook("INFO", f"Auto-dismissed stale plan '{plan_id}' (age: {age_days} days)")
                return None
        except (ValueError, TypeError):
            pass  # Malformed date — fall through to normal gate

    # Check if plan has been reviewed this session
    plan_reviewed_file = CLAUDE_DIR / "state" / "session" / "plan-reviewed-session"
    reviewed_session = ""
    if plan_reviewed_file.exists():
        reviewed_session = plan_reviewed_file.read_text().strip()
    if reviewed_session != ctx.session_id and not is_autonomous_bypass("plan-review"):
        # First tool call with unreviewed plan — inject review gate
        # Mark as reviewed for this session so gate fires only once
        plan_reviewed_file.write_text(ctx.session_id)
        log_hook("WARN", f"Plan review gate: unreviewed plan '{plan_id}'")
        log_gate_metric("plan-review", "warn", plan_id)
        return deny(
            f"[GUARDRAIL: PLAN REVIEW] Cached plan detected: '{plan_id}' (status: active). "
            f"Review or dismiss the plan before proceeding."
        )
    return None


_APPROACH_LEDGER_DIR = CLAUDE_DIR / "state" / "session"
_APPROACH_LEDGER_MAX_ENTRIES = 20


def _approach_ledger_path(session_id: str) -> Path:
    """Per-session read ledger for the approach-verification gate."""
    safe = session_id.replace("/", "_")[:64] or "unknown"
    return _APPROACH_LEDGER_DIR / f"approach-ledger-{safe}.json"


def _approach_ledger_load(session_id: str) -> list:
    path = _approach_ledger_path(session_id)
    if not path.exists():
        return []
    try:
        import json as _json
        data = _json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _approach_ledger_append(session_id: str, file_path: str) -> None:
    """Record a recently-read path, keeping the last N DISTINCT paths.

    Deduplicated, because the window is what the deny message promises the
    user: "the last N files you read". Storing raw append *events* instead
    meant N repeated Reads of one file consumed every slot and evicted all
    the others, so the gate denied an Edit on a file the session really had
    read — the gate's own advertised remedy failing to resolve it (#4495).

    Last-occurrence-wins rather than first, because this is a *recency*
    window: a path just re-read is now the most recent and must not be
    evicted ahead of older ones. Deduplicating the whole list (not only the
    incoming path) also heals a ledger written by the previous code in one
    write, instead of recovering one slot per subsequent Read.
    """
    if not file_path:
        return
    try:
        _APPROACH_LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        entries = _approach_ledger_load(session_id)
        entries.append(file_path)
        # dict preserves insertion order, so keying the REVERSED list keeps
        # each path's last occurrence; reversing back restores oldest-first.
        entries = list(dict.fromkeys(reversed(entries)))
        entries.reverse()
        entries = entries[-_APPROACH_LEDGER_MAX_ENTRIES:]
        import json as _json
        _approach_ledger_path(session_id).write_text(_json.dumps(entries))
    except OSError:
        pass  # Best-effort: ledger failure must not block tool calls


def _approach_target_exempt(tool_name: str, target: str) -> bool:
    """Return True if target is exempt from approach-verification.

    Fresh-writing directories (plans, drafts, reviews) and brand-new
    file creations are out of scope for the read-first ledger check.
    """
    if target.endswith(".md") and (
        "/plans/" in target
        or "/drafts/" in target
        or "/reviews/" in target
    ):
        return True
    if tool_name == "Write" and not os.path.exists(target):
        return True
    return False


def _gate_approach(ctx: GateContext) -> dict | None:
    """Gate V2: Approach verification — block Write/Edit on unread files.

    Require target file to appear in session's read ledger (last 20
    entries) before mutation. Catches the failure mode where an
    autonomous loop edits a file it has never inspected.

    Read/Glob update the ledger in-place. Yolo mode bypasses via the
    ``approach-verification`` ID. Fresh-writing paths and new-file
    creation are exempt via ``_approach_target_exempt``.
    """
    if ctx.tool_name in ("Read", "Glob"):
        target = (ctx.tool_input.get("file_path")
                  or ctx.tool_input.get("path")
                  or "")
        if target:
            _approach_ledger_append(ctx.session_id, target)
        return None
    if ctx.tool_name not in ("Write", "Edit"):
        return None
    if is_autonomous_bypass("approach-verification"):
        return None
    target = ctx.tool_input.get("file_path", "")
    if not target or _approach_target_exempt(ctx.tool_name, target):
        return None
    ledger = _approach_ledger_load(ctx.session_id)
    if target in ledger:
        return None
    log_gate_metric("approach-verification", "deny", target)
    return deny(
        "[GUARDRAIL: APPROACH VERIFICATION] "
        f"{ctx.tool_name} on '{target}' blocked — target not Read in the last "
        f"{_APPROACH_LEDGER_MAX_ENTRIES} operations. Either Read the file "
        "first (updates the ledger automatically) or bypass this gate by "
        "activating yolo mode for autonomous loops."
    )


def _gate_rsi_control(ctx: GateContext) -> dict | None:
    """Gate $: RSI control (stop signal + convergence stall)."""
    block = check_rsi_control_gate()
    if block:
        return block
    return None


def _gate_1_dependency(ctx: GateContext) -> dict | None:
    """Gate 1: Dependency Guardian — block reads from node_modules, venv, etc."""
    if ctx.tool_name not in ("Read", "Glob", "Grep"):
        return None
    path = ctx.tool_input.get("file_path") or ctx.tool_input.get("path") or ""
    pattern = ctx.tool_input.get("pattern", "")

    blocked = is_blocked_path(path) or is_blocked_path(pattern)
    if blocked:
        log_hook("BLOCK", f"Dependency guardian: {blocked}")
        return ctx_deny(ctx,
            f"[GUARDRAIL: DEPENDENCY GUARDIAN] Cannot read from '{blocked}'. "
            f"These folders waste 50k+ tokens. Use rg/fd with --exclude instead."
        )
    return None


def _identity_from_session_file() -> bool:
    """Source 1: session-level active identity (state/session/active-identity)."""
    identity_file = CLAUDE_DIR / "state" / "session" / "active-identity"
    if not identity_file.exists():
        return False
    try:
        return identity_file.read_text().strip() == "V>>"
    except OSError:
        return False


def _identity_from_env() -> bool:
    """Source 2: GRIP_IDENTITY environment variable (inline bash overrides)."""
    return os.environ.get("GRIP_IDENTITY", "") == "V>>"


def _identity_from_facts() -> bool:
    """Source 3: permanent identity from facts/identity.md (agents inherit)."""
    perm_identity = CLAUDE_DIR / "facts" / "identity.md"
    if not perm_identity.exists():
        return False
    try:
        return any(
            "signature:" in line and "V>>" in line
            for line in perm_identity.read_text().splitlines()
        )
    except OSError:
        return False


def _gate_r_restricted_docs(ctx: GateContext) -> dict | None:
    """Gate R: Restricted Docs — require V>> identity.

    Checks for an active N-Mind identity (V>>) from three sources, in
    order: session state, env var, permanent identity (extracted per
    rules/cc-enforcement.md extract-independent-blocks; task #357).
    """
    if ctx.tool_name not in ("Read", "Glob", "Grep"):
        return None
    check_path = ctx.tool_input.get("file_path") or ctx.tool_input.get("path") or ctx.tool_input.get("pattern", "")
    if "docs/restricted/" not in check_path and "docs/restricted" not in check_path:
        return None

    if _identity_from_session_file() or _identity_from_env() or _identity_from_facts():
        return None
    log_hook("BLOCK", f"Restricted docs gate: {check_path}")
    return ctx_deny(ctx,
        "[GUARDRAIL: RESTRICTED DOCS] This document requires identity verification. "
        "Only V>> signature can access restricted documents. "
        "Set identity with the N-Mind system or GRIP_IDENTITY env var."
    )


def _gate_k_cross_org(ctx: GateContext) -> dict | None:
    """Gate K: Cross-Org Read Protection."""
    _k_reason = _gate_k_raw(ctx.tool_name, ctx.tool_input)
    if _k_reason:
        log_gate_metric("cross-org-read", "deny", ctx.target)
        return deny(_k_reason)
    return None


def _gate_l_slack_workspace(ctx: GateContext) -> dict | None:
    """Gate L: Slack Workspace Routing."""
    _l_reason = _gate_l_raw(ctx.tool_name, ctx.tool_input)
    if _l_reason:
        log_gate_metric("slack-workspace-routing", "deny", ctx.target)
        return deny(_l_reason)
    return None


def _gate_m_guest_rsi(ctx: GateContext) -> dict | None:
    """Gate M: Guest RSI Block."""
    _m_reason = _gate_m_raw(ctx.tool_name, ctx.tool_input)
    if _m_reason:
        log_gate_metric("guest-rsi-block", "deny", ctx.target)
        return deny(_m_reason)
    return None


def _gate_bash_pipeline(ctx: GateContext) -> dict | None:
    """Gates 2-6: Bash Security Gates (pipeline)."""
    if ctx.tool_name != "Bash":
        return None
    command = ctx.tool_input.get("command", "")
    _bash_fast_path = _is_safe_bash(command)

    bash_result = run_bash_gates(
        command, ctx.session_id, ctx.tool_name, ctx.tool_input, ctx.transcript_path,
        is_fast_path=_bash_fast_path,
        check_deploy_gate_fn=lambda cmd: _check_deploy_gate_raw(cmd, check_and_consume_approval=_check_and_consume_approval, log_hook=log_hook),
        check_steering_fn=lambda cmd: _check_steering_raw(cmd, log_hook=log_hook),
        check_ssh_blocking_fn=lambda cmd: _check_ssh_blocking_raw(cmd, check_approval=check_destructive_approval, log_hook=log_hook),
        ask_fn=ask,
        is_destructive_git_fn=is_destructive_git,
        is_force_push_fn=_is_force_push,
        is_autonomous_bypass_fn=is_autonomous_bypass,
        check_approval_fn=check_destructive_approval,
        clear_approval_fn=clear_destructive_approval,
        check_git_push_gate_fn=lambda cmd: _check_git_push_gate_raw(cmd, check_approval=check_destructive_approval, is_autonomous_bypass=is_autonomous_bypass, log_hook=log_hook, clear_approval=clear_destructive_approval),
        check_infra_test_gate_fn=lambda cmd: _check_infra_test_gate_raw(cmd, log_hook=log_hook),
        check_stash_pop_safety_fn=lambda cmd: _check_stash_pop_safety_raw(cmd, is_autonomous_bypass=is_autonomous_bypass, log_hook=log_hook),
        check_stash_drop_learning_fn=lambda cmd: _check_stash_drop_raw(cmd, log_hook=log_hook),
        check_branch_switch_fn=lambda cmd: _check_branch_switch_raw(cmd, log_hook=log_hook),
        check_git_index_lock_fn=_check_git_index_lock_raw,
        contains_sensitive_file_fn=contains_sensitive_file,
        check_env_var_exposure_fn=_check_env_var_exposure_raw,
        check_database_exposure_fn=_check_database_exposure_raw,
        check_bash_tools_fn=_check_bash_tools_raw,
        check_bash_lint_fn=_check_bash_lint_raw,
        log_hook_fn=log_hook,
        log_gate_metric_fn=log_gate_metric,
        deny_fn=deny,
        deny_and_log_fn=deny_and_log,
        allow_fn=allow,
        ACTION_FORCE_PUSH=ACTION_FORCE_PUSH,
    )
    if bash_result:
        return bash_result
    return None


def _gate_4_read_cache(ctx: GateContext) -> dict | None:
    """Gate 4: Read Cache — block redundant file reads."""
    if ctx.tool_name != "Read":
        return None
    file_path = ctx.tool_input.get("file_path", "")
    if ctx.tool_input.get("offset") is None:
        _rc_reason = _check_read_cache_raw(file_path, ctx.session_id, max_reads=_get_gate_thresholds().read_cache_max)
        if _rc_reason:
            return deny(_rc_reason)
    return None


def _gate_7_temp_script(ctx: GateContext) -> dict | None:
    """Gate 7: Temp Script Prevention."""
    if ctx.tool_name != "Write":
        return None
    file_path = ctx.tool_input.get("file_path", "")
    _thresholds = _get_gate_thresholds()
    _temp_result = _check_temp_script_raw(file_path, is_autonomous=is_autonomous_bypass("temp-script"), temp_action=_thresholds.temp_script_action)
    if _temp_result:
        _td, _tr = _temp_result
        if _td == "deny":
            log_hook("BLOCK", f"Temp script: {file_path}")
            return deny(_tr)
        else:
            log_hook("WARN", f"Temp script (relaxed): {file_path}")
            return allow(_tr)
    return None


def _gate_n_target_dir(file_path: str) -> str:
    """Nearest EXISTING ancestor of the target file, cwd only as last resort.

    Issue #4382: when ``.github/workflows/`` does not exist yet — the
    CI-bootstrap case — ``dirname()`` names a not-yet-created directory, and
    the old isdir->cwd fallback misattributed the SESSION's repo, defeating
    the 2026-05-24 cross-repo fix. Three independent hits on 2026-08-06
    (hal-go / hal-js / hal-conformance bootstrap PRs)."""
    import os
    target_dir = os.path.dirname(os.path.abspath(file_path)) if file_path else ""
    while target_dir and target_dir != os.path.dirname(target_dir) \
            and not os.path.isdir(target_dir):
        target_dir = os.path.dirname(target_dir)
    if not target_dir or not os.path.isdir(target_dir):
        target_dir = os.getcwd()
    return target_dir


def _gate_n_is_workflow_bootstrap(target_dir: str, file_path: str) -> bool:
    """True when the target repo has NO existing workflow file besides the
    target itself — then there is nothing whose triggers a PR branch could
    suppress, the exact carve-out Gate N's deny text has always sanctioned
    (the three 2026-08-06 bootstrap PRs ran their fresh workflows green on
    their own pull_request events). Fails toward protection on any error."""
    import os
    import subprocess
    try:
        repo_root = subprocess.run(
            ["git", "-C", target_dir, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if not repo_root:
            return False
        wf_dir = os.path.join(repo_root, ".github", "workflows")
        target_abs = os.path.abspath(file_path)
        existing = [
            f for f in (os.listdir(wf_dir) if os.path.isdir(wf_dir) else [])
            if f.endswith((".yml", ".yaml"))
            and os.path.join(wf_dir, f) != target_abs
        ]
        return not existing
    except Exception:
        return False


def _gate_n_branch(target_dir: str) -> str:
    """The TARGET file's repo branch — NOT cwd's repo branch.

    Cross-repo writes (e.g. a ~/.claude session editing
    ~/CodeTonight/grip-post/.github/workflows/ci.yml while ~/.claude is on a
    feat/* branch) must scope to the target repo's HEAD, not the caller's.
    Origin 2026-05-24: W1 grip-post bootstrap false-positive.

    `branch --show-current` returns the branch name even on an unborn HEAD
    (fresh `git init`, pre-first-commit). `rev-parse --abbrev-ref HEAD` returns
    the literal "HEAD" there and would mis-block bootstrap commits on `main`.
    """
    import subprocess
    try:
        return subprocess.run(
            ["git", "-C", target_dir, "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        return ""


def _gate_n_fork_deny(ctx, branch: str, target_dir: str, file_path: str) -> dict:
    """The surviving deny: a workflow edit on a branch of a FORK checkout."""
    log_hook("BLOCK", f"Gate N: workflow edit on FORK PR branch '{branch}' (repo='{target_dir}'): {file_path}")
    log_gate_metric("workflow-protection", "block", file_path)
    return ctx_deny(ctx,
        f"[Gate N] BLOCKED: Editing .github/workflows/ on branch '{branch}' in the FORK "
        f"checkout '{target_dir}'. GitHub does not run fork pull-request workflows "
        f"automatically for outside contributors — a maintainer must approve them — and "
        f"fork PRs receive no secrets and a read-only GITHUB_TOKEN, so CI on this change "
        f"will not behave like CI on main. Edit workflow files on main, or open the "
        f"workflow-only PR from a branch in the upstream repository itself."
    )


def _gate_n_is_fork_checkout(target_dir: str) -> bool:
    """True when the checkout is a FORK — the only case where Gate N's premise
    actually holds.

    Gate N used to deny EVERY workflow edit off main, justified by "GitHub
    suppresses pull_request events when a PR modifies workflow files". That is
    false for SAME-REPO branches, and was refuted three times in GRIP itself:
    PR #4745 (mirror-daily.yml + windows-smoke.yml -> 12 checks ran), PR #5531
    (dependency-audit.yml -> 12 checks ran), and a third instance 2026-09-13.
    Both PRs report isCrossRepository=false, so the branch was in the repo.

    What GitHub actually documents is a FORK restriction
    (github/docs data/reusables/actions/workflow-run-approve-public-fork.md):
    "Anyone can fork a public repository, and then submit a pull request that
    proposes changes to the repository's GitHub Actions workflows. ... To help
    prevent this, workflows on pull requests to public repositories from some
    outside contributors will not run automatically, and might need to be
    approved first." Forks additionally get no secrets (forked-secrets.md) and
    a read-only GITHUB_TOKEN. None of that applies to a branch in the repo.

    Detection, cheapest first:
      1. a distinct `upstream` remote beside `origin` — the `gh repo fork`
         layout; offline and instant.
      2. `gh repo view --json isFork` — authoritative, and the only signal that
         catches a clone whose `origin` IS the fork.
      3. unknown -> False (treat as not-a-fork).

    Step 3 deliberately resolves toward ALLOW. Failing closed here would
    re-create the exact deadlock this change exists to remove
    (rules/gate-resolution-escape-hatch.md Invariant 1: the gate denied the
    workflow-only PR its own deny text prescribed, so only an out-of-band actor
    could recover) and would do so for every offline session. The residual risk
    accepted is bounded and recoverable: an undetected fork contributor loses a
    warning, and their PR's workflows wait for maintainer approval — annoying,
    not unsafe.
    """
    import subprocess

    def _git(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", "-C", target_dir, *args],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:
            return ""

    upstream = _git("config", "--get", "remote.upstream.url")
    if upstream and upstream != _git("config", "--get", "remote.origin.url"):
        return True
    try:
        probe = subprocess.run(
            ["gh", "repo", "view", "--json", "isFork", "-q", ".isFork"],
            cwd=target_dir, capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return False
    return probe.returncode == 0 and probe.stdout.strip() == "true"


def _gate_n_workflow(ctx: GateContext) -> dict | None:
    """Gate N: Workflow File Protection — block workflow edits on FORK PR
    branches, where GitHub genuinely withholds automatic workflow runs."""
    if ctx.tool_name not in ("Write", "Edit"):
        return None
    file_path = ctx.tool_input.get("file_path", "")
    if "/.github/workflows/" not in file_path and not file_path.startswith(".github/workflows/"):
        return None

    target_dir = _gate_n_target_dir(file_path)
    if _gate_n_is_workflow_bootstrap(target_dir, file_path):
        log_hook("ALLOW", f"Gate N: workflow bootstrap (no existing workflows in target repo): {file_path}")
        log_gate_metric("workflow-protection", "bootstrap_allow", file_path)
        return None
    branch = _gate_n_branch(target_dir)
    if not branch or branch in ("main", "master"):
        return None
    # Narrowed 2026-09-13 to FORK checkouts. On a same-repo branch the old
    # premise is measurably false (GRIP PRs #4745 and #5531 each changed a
    # workflow off main and ran all 12 checks), and denying there blocked the
    # very workflow-only PR this gate's own deny text prescribed —
    # rules/gate-resolution-escape-hatch.md Invariant 1.
    if not _gate_n_is_fork_checkout(target_dir):
        log_hook("ALLOW", f"Gate N: same-repo branch '{branch}' is not a fork (repo='{target_dir}'): {file_path}")
        log_gate_metric("workflow-protection", "same_repo_allow", file_path)
        return None
    return _gate_n_fork_deny(ctx, branch, target_dir, file_path)


_DETECTION_PATTERN_TESTS = frozenset({
    # reference the env-pattern constants (measured 2026-08-15)
    "test_gate_extraction.py",
    "test_security_audit.py",
    "test_lazy_patterns.py",
    # exercise the env-var checker itself
    "test_enforcement_gates.py",
    "test_gate_orchestrators.py",
    "test_orchestrator_preprocessing.py",
    "test_gate_detection_pattern_exemption.py",
    # quote forbidden shapes to prove the secrets/guardrail detectors fire
    "test_guardrail_evasion_detector.py",
    "test_guardrail_evasion_mutation.py",
    "test_guardrail_removal_detector.py",
    "test_user_prompt_secrets_hook.py",
    "test_gate3_state_dotfiles.py",
})


def _is_detection_pattern_test(file_path: str) -> bool:
    """True for a test whose job is to prove a detection pattern fires.

    Two conditions, both required: the basename is on the enumerated allowlist,
    AND it sits directly in a tests/ directory. The second is what carries the
    safety -- without it a production module could take the exemption by
    matching a name.

    Restored 2026-08-18: PR #4896's wholesale copy of this file silently dropped
    this exemption (added by #4767), so writing one of these test files tripped
    Gate D's env-var-exposure check. The regression was masked by the gate
    suite's failure-tolerance in CI; tests/test_gate_detection_pattern_exemption.py
    (38 cases) is the anchor that would have caught it.
    """
    if not file_path:
        return False
    parts = file_path.replace("\\", "/").split("/")
    if len(parts) < 2 or parts[-2] != "tests":
        return False
    return parts[-1] in _DETECTION_PATTERN_TESTS


def _gate_d_network_binding(ctx: GateContext) -> dict | None:
    """Gate D: Network Binding Protection."""
    if ctx.tool_name not in ("Write", "Edit"):
        return None
    file_path = ctx.tool_input.get("file_path", "")
    if file_path.endswith(".md"):
        return None
    content = ctx.tool_input.get("content", "") or ctx.tool_input.get("new_string", "") or ctx.tool_input.get("newText", "")
    if content:
        _bind_reason = _check_network_binding_raw(content, log_hook=log_hook)
        if _bind_reason:
            return deny(_bind_reason)
    if _is_detection_pattern_test(file_path):
        return None
    _env_w_reason = _check_env_var_exposure_raw("", content)
    if _env_w_reason:
        return deny(_env_w_reason)
    return None


def _gate_8_creation(ctx: GateContext) -> dict | None:
    """Gate 8: Skill/Mode/Agent Creation Gate."""
    if ctx.tool_name not in ("Write", "Edit"):
        return None
    file_path = ctx.tool_input.get("file_path", "")
    is_creation_path = (
        ("/skills/" in file_path and file_path.endswith(".md"))
        or ("/modes/definitions/" in file_path and file_path.endswith(".yaml"))
        or ("/agents/" in file_path and file_path.endswith(".md"))
    )
    if not is_creation_path:
        return None

    thresholds = _get_gate_thresholds()
    if thresholds.skill_creation_action == "deny":
        log_gate_metric("skill-creation", "block", file_path)
        return deny(
            "[SKILL CREATION] BLOCKED — trust level STRICT. "
            "Earn higher convergence before creating skills."
        )
    elif thresholds.skill_creation_action == "allow":
        log_gate_metric("skill-creation", "allow", file_path)
        # Silent allow at RELAXED trust level — return allow() to terminate
        return allow()
    else:
        # Default: warn
        log_gate_metric("skill-creation", "allow", file_path)
        return allow(
            "[CREATION GATE] New skill/mode/agent file detected. Check for existing duplicates first."
        )


def _gate_q_complexity(ctx: GateContext) -> dict | None:
    """Gate Q: Complexity Gate — warn on O(n^2)+ nested loops in Python."""
    if ctx.tool_name not in ("Write", "Edit"):
        return None
    file_path = ctx.tool_input.get("file_path", "")
    if not file_path.endswith(".py"):
        return None
    content = ctx.tool_input.get("content", "") or ctx.tool_input.get("new_string", "")
    if not content or not _detect_nested_loops(content):
        return None

    thresholds = _get_gate_thresholds()
    if thresholds.complexity_ceiling == "n":
        # STRICT: O(n) ceiling — warn on any nested loop
        log_gate_metric("complexity", "warn", file_path)
        return allow(
            "[COMPLEXITY: O(n^2)+] Nested loop detected. "
            "Trust level STRICT — O(n) ceiling. Refactor to linear."
        )
    else:
        # DEFAULT/RELAXED: O(n^2) ceiling — warn but with note
        log_gate_metric("complexity", "warn", file_path)
        return allow(
            "[COMPLEXITY: O(n^2)+] Nested loop detected. "
            "BIG-O principle: justify or refactor to O(n log n)."
        )


def _gate_9_context(ctx: GateContext) -> dict | None:
    """Gate 9: Context Gate — token usage thresholds."""
    context_result = run_context_gate(
        ctx.transcript_path, ctx.session_id, ctx.tool_name, ctx.tool_input,
        autonomous_mode=AUTONOMOUS_MODE,
        context_is_extended=CONTEXT_IS_EXTENDED,
        is_subagent=bool(ctx.agent_id),
        log_hook_fn=log_hook,
        log_gate_metric_fn=log_gate_metric,
        deny_fn=deny,
        allow_fn=allow,
    )
    if context_result:
        return context_result
    return None


def _gate_steering(ctx: GateContext) -> dict | None:
    """Steering Context Injection — interrupt current tool for steering directives."""
    inject_ids = []
    instance_uuid = _get_instance_uuid()
    if instance_uuid:
        inject_ids.append(instance_uuid)
    if ctx.session_id:
        inject_ids.append(ctx.session_id)
    for sid in inject_ids:
        from _compat import grip_injection_path
        inject_path = Path(grip_injection_path(sid))
        try:
            if inject_path.exists() and inject_path.stat().st_size > 0:
                injected_message = inject_path.read_text().strip()
                inject_path.write_text("")
                if injected_message:
                    log_hook("INFO", f"Steering context received: {len(injected_message)} chars")
                    # DENY the current tool call — forces agent to address steering
                    # before proceeding. File is already cleared, so next call passes.
                    log_gate_metric("steering-interrupt", "deny")
                    if AUDIT_ENABLED:
                        log_tool_call(ctx.session_id, ctx.tool_name, ctx.tool_input, "deny", ctx.transcript_path)
                    return deny(
                        f"[STEERING CONTEXT] {injected_message}\n\n"
                        "HALT current task. Address the steering directive above before continuing."
                    )
                break
        except OSError:
            pass
    return None


def _gate_t_tdd_nudge(ctx: GateContext) -> dict | None:
    """Gate T: TDD Nudge — warn when writing to lib/ without a test file.

    WARN only (advisory, does not block). Nudges test-first development
    by checking if a corresponding tests/test_<name>.py exists before
    allowing writes to lib/<name>.py.

    Origin: #1401 hardworker-marketplace absorption (R2 TDD gate).
    """
    if ctx.tool_name not in ("Write", "Edit"):
        return None
    file_path = ctx.tool_input.get("file_path", "")
    if not file_path:
        return None

    # Only check lib/*.py files
    match = re.search(r'/lib/([^/]+)\.py$', file_path)
    if not match:
        return None

    module_name = match.group(1)
    # Skip __init__.py, test files, and known infrastructure
    if module_name.startswith('__') or module_name.startswith('test_'):
        return None

    # Check if corresponding test file exists
    lib_dir = file_path[:file_path.rfind('/lib/')]
    test_path = f"{lib_dir}/tests/test_{module_name}.py"

    if not Path(test_path).exists():
        log_hook("WARN", f"[Gate T] No test file for {module_name}.py — consider creating tests/test_{module_name}.py")
        return allow(
            f"[TDD NUDGE] No test file found for lib/{module_name}.py. "
            f"Consider creating tests/test_{module_name}.py before modifying."
        )
    return None


# Speculative language patterns that indicate unfinished code
_SPECULATIVE_PATTERNS = [
    (r'\bTODO\b', "TODO"),
    (r'\bFIXME\b', "FIXME"),
    (r'\bHACK\b', "HACK"),
    (r'\bplaceholder\b', "placeholder"),
    (r'\btemporary\b', "temporary"),
    (r'\bworkaround\b', "workaround"),
    (r'should work', "should work"),
]


def _gate_u_speculative_pattern(ctx: GateContext) -> dict | None:
    """Gate U: Speculative Pattern Detection — warn on speculative code.

    WARN only (advisory, does not block). Detects TODO, FIXME, HACK,
    placeholder, temporary, workaround, and "should work" in code being
    written. Skips markdown, SKILL.md, and documentation files.

    Origin: #1401 hardworker-marketplace absorption (R1 blocked-pattern gate).
    """
    if ctx.tool_name not in ("Write", "Edit"):
        return None
    file_path = ctx.tool_input.get("file_path", "")
    if not file_path:
        return None

    # Skip non-code files
    if file_path.endswith(('.md', '.txt', '.json', '.yaml', '.yml', '.toml', '.cfg')):
        return None
    if '/skills/' in file_path or '/docs/' in file_path:
        return None

    # Check content for speculative patterns
    content = ctx.tool_input.get("content", "") or ctx.tool_input.get("new_string", "")
    if not content:
        return None

    found = []
    for pattern, label in _SPECULATIVE_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            found.append(label)

    if found:
        patterns_str = ", ".join(found)
        log_hook("WARN", f"[Gate U] Speculative patterns in {file_path}: {patterns_str}")
        return allow(
            f"[SPECULATIVE CODE] Found {patterns_str} in code. "
            f"Consider resolving before committing."
        )
    return None


# =========================================================================
# GATE V: TWO-HOOK GATE — PostToolUse Violation Enforcement
# Reads violation state written by quality-posttool-hook.py.
# BLOCKS the next tool call until the violation is fixed (cleared by PostToolUse).
# This bridges the PostToolUse advisory gap with JIT enforcement.
# =========================================================================

_ENFORCEMENT_DIR = CLAUDE_DIR / "state" / "enforcement"
# Bash included: Gate V is quality enforcement, not security. Bash security
# is handled by Gates 2/3/5/6/F/H downstream in the pipeline.
_READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "Bash"}

# HITL-essential tools: never blocked by Gate V. User consultation and plan-mode
# exit are orthogonal to quality violations — they do not write code, and blocking
# them leaves the agent unable to ask for guidance or exit plan mode cleanly.
# When the upstream regression (anthropics/claude-code#39342) is fixed and these
# tools become available again, this set ensures they remain callable during
# violation enforcement.
_HITL_ESSENTIAL_TOOLS = {"AskUserQuestion", "ExitPlanMode", "EnterPlanMode"}


def _get_cc_pid_for_gate():
    """Derive Claude Code PID for session-scoped state (same as PostToolUse writer)."""
    try:
        import subprocess as _sp
        ppid = os.getppid()
        result = _sp.run(["ps", "-o", "ppid=", "-p", str(ppid)],
                         capture_output=True, text=True, timeout=1)
        cc_pid = result.stdout.strip()
        if cc_pid and cc_pid != "1":
            return cc_pid
    except Exception:
        pass
    return str(os.getpid())


def _violation_file_for_session():
    """Legacy single-file path (#2837 backward compat — still read if present)."""
    return _ENFORCEMENT_DIR / f"quality-violation-{_get_cc_pid_for_gate()}.json"


# Once-per-process sentinel for legacy-prune (#3126 advisory #4).
_LEGACY_PRUNE_RAN = False


def _iter_violation_files():
    """Yield ALL pending violation files for this session (#2837 per-path scoping).

    Returns the legacy single-file plus every per-path file. Gate V then
    matches the inbound tool's target against each — only blocks if THIS
    target's path is one of the stored violating paths. Unrelated paths
    pass through (the cascade #2837 fixes).

    On the FIRST call per process, prunes any legacy
    ``quality-violation-<PID>.json`` orphan older than 24h
    (pre-#2837 crash leftovers — #3126 advisory #4). The 180s TTL
    inside ``_load_violation_or_unlink`` only fires when a file is
    actually parsed, so a never-touched orphan can sit forever; this
    prune sweeps them on the first gate fire of each session.
    """
    global _LEGACY_PRUNE_RAN
    if not _LEGACY_PRUNE_RAN:
        _LEGACY_PRUNE_RAN = True
        _prune_legacy_violation_files()
    pid = _get_cc_pid_for_gate()
    legacy = _ENFORCEMENT_DIR / f"quality-violation-{pid}.json"
    if legacy.exists():
        yield legacy
    try:
        for p in _ENFORCEMENT_DIR.glob(f"quality-violation-{pid}-*.json"):
            yield p
    except OSError:
        return


def _violation_targets_match(ctx, target_file):
    """Check if the tool is allowed during violation enforcement.

    Read-only tools always pass — they do not trigger PostToolUse quality
    checks and are needed to investigate and fix existing ones (Read before
    Edit, Grep for context, Bash for validation). Bash security is handled
    by Gates 2/3/5/6/F/H downstream. Only Write/Edit are restricted to the
    violating file to prevent writing more bad code elsewhere. HITL-essential
    tools (AskUserQuestion, ExitPlanMode, EnterPlanMode) are always permitted
    — user consultation is orthogonal to quality enforcement.
    """
    if ctx.tool_name in _READ_ONLY_TOOLS:
        return True
    if ctx.tool_name in {"Write", "Edit"}:
        return target_file in ctx.target
    return ctx.tool_name in _HITL_ESSENTIAL_TOOLS


def _format_violation_reasons(violation):
    """Build deny reason string from violation state."""
    reasons = []
    if violation.get("syntax_error"):
        reasons.append(violation["syntax_error"])
    for v in violation.get("violations", [])[:3]:
        reasons.append(f"[{v['principle']}] {v['message']}")
    return "; ".join(reasons) or "quality violation pending"


def _v_yolo_bypass(gate: str) -> bool:
    """Probe yolo_quality bypass — mirrors quality-pretool semantics (#2885).

    ``gate`` is now required (no silent default). If yolo-bypass ever becomes
    per-gate, callers that forgot to pass the right name will fail loud
    rather than silently inherit ``quality-pretool`` (#3126 advisory #2).
    """
    try:
        from gates.yolo_mode import is_yolo_bypass
        return is_yolo_bypass(gate)
    except Exception:
        return False


def _is_test_path(path: str) -> bool:
    """Pure predicate: does the given file path live under a test tree?

    Extracted from ``_load_violation_or_unlink`` so reader/parser stays
    pure and policy lives at the call-site (#3126 advisory #1). The old
    in-reader side effect (``vf.unlink``) could race with concurrent
    ``_iter_violation_files()`` callers; the new shape moves the unlink
    decision to ``_gate_v_violation_enforcement`` where the policy belongs.
    """
    return bool(path) and ("/test_" in path or "/tests/" in path)


def _load_violation_or_unlink(vf) -> dict | None:
    """Read one violation file. Pure read + parse + TTL check.

    Returns the decoded violation dict, or ``None`` if the file is
    missing / malformed / expired. The test-path exemption is no longer
    applied here (it was a side-effecting policy in a reader — see
    ``_is_test_path``); the caller decides what to do with a test-path
    violation.
    """
    import time as _time
    try:
        violation = json.loads(vf.read_text())
    except (json.JSONDecodeError, OSError, KeyError):
        vf.unlink(missing_ok=True)
        return None
    if _time.time() - violation.get("timestamp", 0) > 180:
        vf.unlink(missing_ok=True)
        return None
    return violation


def _prune_legacy_violation_files(max_age_seconds: int = 86_400) -> int:
    """Prune legacy single-file ``quality-violation-<PID>.json`` artefacts
    older than ``max_age_seconds`` (default 24h).

    Pre-#2837 crash leftovers can surface and falsely block a path that
    was resolved in a prior session — the 180s TTL inside
    ``_load_violation_or_unlink`` only fires when the legacy file is
    actually parsed, so a never-touched orphan can sit forever
    (#3126 advisory #4). Returns the count of pruned files (for tests
    + telemetry).
    """
    import time as _time
    pruned = 0
    cutoff = _time.time() - max_age_seconds
    try:
        for legacy in _ENFORCEMENT_DIR.glob("quality-violation-*.json"):
            try:
                if legacy.stat().st_mtime < cutoff:
                    legacy.unlink(missing_ok=True)
                    pruned += 1
            except OSError:
                continue
    except OSError:
        pass
    return pruned


def _is_foreign_actor_marker(violation: dict, ctx) -> bool:
    """True when a marker was stamped by a DIFFERENT actor than ``ctx``."""
    writer_agent = violation.get("agent_id")
    # Actor-scoped enforcement (#342): subagents share the parent
    # Claude Code PID, so a fleet agent's marker would otherwise
    # stop-the-line the parent's unrelated edits (and vice versa).
    # A marker only gates the actor that wrote it. Missing stamp
    # (pre-#342 marker) = legacy = enforce. NOT unlinked: the
    # writer's own line stays gated; the 180s TTL bounds cleanup.
    # Non-string stamp (corrupt marker) = legacy = enforce (fail-closed).
    return isinstance(writer_agent, str) and writer_agent != getattr(ctx, "agent_id", "")


def _gate_v_violation_enforcement(ctx: GateContext) -> dict | None:
    """Block tool calls only when THIS target's path has a pending violation (#2837).

    Per-path scoping replaces the legacy session-global flag. We iterate
    every pending violation file for this session and only deny if the
    inbound tool's target matches one of the stored violating paths.
    Violations on unrelated paths (other worktrees, sibling files, parallel
    bg agents) no longer cascade-block the orchestrator or each other.

    Policy applied at the call-site (#3126 advisory #1): test-path
    violations are silently dropped HERE, not inside the reader. The
    reader stays pure.
    """
    if _v_yolo_bypass("quality-pretool"):
        for vf in _iter_violation_files():
            vf.unlink(missing_ok=True)
        return None
    for vf in _iter_violation_files():
        violation = _load_violation_or_unlink(vf)
        if not violation:
            continue
        target_file = violation.get("file_path", "")
        if not target_file:
            continue
        if _is_test_path(target_file):
            # Test files are exempt from quality enforcement
            # (rules/learned-insights.md "Dry"). Policy lives here, not
            # in the reader (#3126 advisory #1).
            vf.unlink(missing_ok=True)
            continue
        if _is_foreign_actor_marker(violation, ctx):
            continue
        if _violation_targets_match(ctx, target_file):
            return None
        log_hook("DENY", f"[Gate V] Blocking {ctx.tool_name}: violation in {target_file}")
        return deny(
            f"[GUARDRAIL: VIOLATION ENFORCEMENT] Fix quality violation in {target_file} "
            f"before proceeding. Violations: {_format_violation_reasons(violation)}. "
            f"Write/Edit the file to fix, then PostToolUse will clear the gate."
        )
    return None


def _session_in_grip(cwd: str) -> bool:
    """True only when the session works inside a GRIP checkout. Fails closed.

    The confidence council is an outside model, so a session anywhere else gets
    the heuristic alone; its verdict is still derived from the score, never a
    forced pass (lib/grip_checkout.py).
    """
    if not cwd:
        return False
    try:
        from grip_checkout import grip_checkout_root
        return grip_checkout_root(cwd) is not None
    except Exception:  # noqa: BLE001 -- a fault here keeps the tool call on this machine
        return False


def _gate_confidence(ctx: GateContext) -> dict | None:
    """Gate: confidence measurement via confidence_gate.py. Allow on any failure.

    Honours yolo mode via ``is_autonomous_bypass("confidence-gate")`` — when
    /ultrado or /rsi-optimal has flipped the yolo marker, the council is not
    consulted and the tool call proceeds. The quality-dry/KISS/BIG-O gates
    keep firing because they are not in the yolo bypass set.
    """
    if is_autonomous_bypass("confidence-gate"):
        log_hook("ALLOW", "Confidence gate bypassed by yolo mode / GRIP_AUTONOMOUS")
        return None

    try:
        from confidence_gate import (  # lazy import
            MAX_CONTEXT_CHARS, as_hook_response, measure_confidence,
        )

        transcript_context = ""
        if ctx.transcript_path:
            from pathlib import Path as _Path
            tp = _Path(ctx.transcript_path)
            if tp.exists():
                # One constant governs both cuts: this one and the confidence
                # gate's own. A literal here made that constant dead (GRIP#8023).
                transcript_context = tp.read_text(errors="replace")[-MAX_CONTEXT_CHARS:]

        result = measure_confidence(ctx.tool_name, ctx.tool_input, transcript_context,
                                    skip_council=not _session_in_grip(ctx.cwd))
        log_hook("INFO", f"Confidence: {result.confidence:.0%} tier={result.tier_reached}")
        if not result.passed:
            return as_hook_response(result)
        return None
    except Exception as exc:
        log_hook("INFO", f"[Gate Confidence] measurement failed, allowing: {exc}")
        return None


# =========================================================================
# GATE PIPELINE — ordered list of gate functions
# Each returns dict to terminate, None to continue to next gate
# =========================================================================
GATE_PIPELINE = [
    _gate_j_mcp_apps,
    _gate_p2_plan_iteration,        # advisory: appends to ctx.advisories
    _gate_s_sequential_rendering,   # advisory: appends to ctx.advisories
    _gate_c_remote,
    _gate_steering,                 # MOVED from last: pair-mode interrupts before Gate 0 short-circuit
    _gate_v_violation_enforcement,  # Two-Hook Gate: blocks until PostToolUse violations are fixed
    _gate_0_always_allow,
    _gate_confidence,               # Confidence gate: measures certainty, denies on low confidence
    _gate_autonomous_consent,
    _gate_rsi_control,
    _gate_approach,                 # V2: blocks Write/Edit on unread files (yolo-bypassable)
    _gate_1_dependency,
    _gate_r_restricted_docs,
    _gate_k_cross_org,
    _gate_l_slack_workspace,
    _gate_m_guest_rsi,
    _gate_bash_pipeline,
    _gate_4_read_cache,
    _gate_7_temp_script,
    _gate_n_workflow,
    _gate_d_network_binding,
    _gate_8_creation,
    _gate_q_complexity,
    _gate_t_tdd_nudge,
    _gate_u_speculative_pattern,
    _gate_9_context,
]


# =========================================================================
# GATE FAILURE ISOLATION (#4809 Fix B)
# =========================================================================
# Before this, the pipeline loop in main() had no try/except and main() caught
# only json.JSONDecodeError. An unhandled exception in ANY of the 26 gates
# therefore exited non-zero with no stdout — and the harness IGNORES that, so
# the gate silently failed OPEN (the behaviour tests/test_hook_output_schema.py
# already documents). Meanwhile config/hooks.registry.json declared this hook
# failure_mode "closed". The metadata was aspirational; the code was not.
#
# A blanket deny-on-any-error is not the fix either: one transient bug would
# block every tool call in every session, which is precisely the deadlock class
# rules/gate-resolution-escape-hatch.md exists to prevent. So failures are
# isolated per gate, and the split is derived from what each gate can actually
# return.
#
# ADVISORY = a gate whose every terminating return is allow(...) or None, so it
# can never produce a deny. Losing it to a crash loses a message, never an
# enforcement verdict — it may fail open, but never silently. Derived by AST
# over GATE_PIPELINE and re-derived on every run of
# tests/test_gate_failure_isolation.py, so a gate that gains a deny cannot
# quietly stay on this list.
ADVISORY_GATES = frozenset({
    "_gate_p2_plan_iteration",       # returns None only; appends to ctx.advisories
    "_gate_s_sequential_rendering",  # returns None only; appends to ctx.advisories
    "_gate_0_always_allow",          # returns None only; metric + ask-time notify
    "_gate_q_complexity",            # terminates with allow(...) advisories only
    "_gate_t_tdd_nudge",             # terminates with allow(...) advisories only
    "_gate_u_speculative_pattern",   # terminates with allow(...) advisories only
})

# Tools left open when a SECURITY gate crashes. This is the resolution set
# demanded by rules/gate-resolution-escape-hatch.md Invariant 1: a blocking gate
# must leave open the operations that resolve its own blocked condition. To fix
# a crashing gate you must read it (Read/Grep/Glob), ask the operator
# (AskUserQuestion — the exact tool the 2026-06-08 deadlock denied while its own
# deny message demanded it), and edit the gate source. Bash, network, deploy and
# every other destructive surface stay denied.
GATE_FAILURE_EXEMPT_TOOLS = frozenset({"Read", "Grep", "Glob", "AskUserQuestion"})

# Write/Edit stays open only against the gate implementation itself.
#
# This tuple used to end in a bare "lib", which subsumed "lib/gates" and every
# other module in the tree: with a security gate crashed, Edit was ALLOWED on
# lib/idr_forest.py, lib/prove_it.py and lib/whatsapp_transport.py. That
# contradicted this comment and the deny text below, and the escape-hatch
# predicate could not see it (it only probed a hooks/ path, so a widening left
# it green). The hatch is the repair set, not a write amnesty.
#
# Falsifier: if a crashed gate ever traces to a module OUTSIDE these two
# directories that Read/Grep/Glob plus AskUserQuestion cannot get repaired, the
# set is too narrow — widen it here AND in the deny text, which is now built
# from this tuple so the two cannot drift apart again.
GATE_FAILURE_REPAIR_DIRS = ("hooks", "lib/gates")

GATE_FAILURE_LOG = CLAUDE_DIR / "data" / "gate-failures.jsonl"


def _grip_checkout_containing(path: Path) -> Path | None:
    """The GRIP checkout that contains `path`, or None if it is not in one.

    Walks up for a `.git` entry — a directory in a clone, a FILE in a worktree,
    so `.exists()` is what admits both. A repo only counts as a GRIP checkout
    if it actually carries the repair directories, which keeps an unrelated
    project's `hooks/` from qualifying just by having that name.
    """
    for parent in (path, *path.parents):
        if not (parent / ".git").exists():
            continue
        if all((parent / rel).is_dir() for rel in GATE_FAILURE_REPAIR_DIRS):
            return parent
        return None
    return None


def _is_under(path: Path, base: Path) -> bool:
    """True when `path` is `base` or lies below it. `base` is resolved first."""
    try:
        path.relative_to(base.resolve())
    except ValueError:
        return False
    return True


def _in_repair_dirs(resolved: Path, root: Path) -> bool:
    """True when `resolved` lies in one of `root`'s gate-repair directories."""
    return any(_is_under(resolved, root / rel) for rel in GATE_FAILURE_REPAIR_DIRS)


def _is_gate_repair_target(target: str) -> bool:
    """True when `target` is a file inside GRIP's own gate implementation.

    Matched against the checkout that CONTAINS the target, not only the one at
    CLAUDE_DIR. Anchoring to that single absolute prefix meant a worktree
    session's own hooks/ was not recognised as gate-repair code, so a crashed
    gate blocked the very edit that would fix it — the deadlock shape Invariant
    1 of gate-resolution-escape-hatch.md exists to forbid, reproduced live.

    The directory list is NOT widened. lib/idr_forest.py must still come back
    deny from every tree; that result is the control proving this is a narrow
    hatch rather than a general amnesty.

    The work is bounded: at most two roots (CLAUDE_DIR and the checkout that
    holds the target) and the two-entry GATE_FAILURE_REPAIR_DIRS, so at most
    four directory checks whatever the target is.
    """
    if not target:
        return False
    try:
        resolved = Path(target).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        return False

    roots = [CLAUDE_DIR]
    containing = _grip_checkout_containing(resolved)
    if containing is not None and containing not in roots:
        roots.append(containing)

    return any(_in_repair_dirs(resolved, root) for root in roots)


def _record_gate_failure(gate_name: str, exc: BaseException, ctx: GateContext) -> str:
    """Log a crashed gate loudly and return the traceback text.

    Loud means three places, because a silent gate failure is the whole defect:
    the shared hook log, a machine-readable telemetry line, and the message the
    session actually sees (deny reason or advisory).
    """
    trace = traceback.format_exc()
    log_hook("ERROR", f"GATE CRASHED: {gate_name} on {ctx.tool_name}: {exc!r}\n{trace}")
    try:
        GATE_FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        _locked_append(str(GATE_FAILURE_LOG), json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "gate": gate_name,
            "advisory": gate_name in ADVISORY_GATES,
            "tool_name": ctx.tool_name,
            "session_id": ctx.session_id,
            "error": repr(exc),
            "traceback": trace,
        }))
    except Exception:  # noqa: BLE001 — telemetry must never mask the failure itself
        pass
    return trace


def _handle_gate_failure(gate_fn, exc: BaseException, ctx: GateContext) -> dict | None:
    """Decide what a crashed gate means. Returns a deny response, or None to continue.

    Advisory gate  -> continue, but surface the crash in the session.
    Security gate  -> DENY, unless the call is in the escape-hatch resolution set.
    """
    gate_name = getattr(gate_fn, "__name__", str(gate_fn))
    _record_gate_failure(gate_name, exc, ctx)

    if gate_name in ADVISORY_GATES:
        ctx.advisories.append(
            f"[GATE FAILURE] Advisory gate {gate_name} crashed ({exc!r}); its check was "
            f"skipped. No enforcement was lost. See logs/hooks.log and data/gate-failures.jsonl."
        )
        return None

    if ctx.tool_name in GATE_FAILURE_EXEMPT_TOOLS:
        ctx.advisories.append(
            f"[GATE FAILURE] Security gate {gate_name} crashed ({exc!r}). {ctx.tool_name} is "
            f"allowed so you can diagnose and repair it; every other tool is denied until it is fixed."
        )
        return None

    if ctx.tool_name in ("Write", "Edit") and _is_gate_repair_target(ctx.target):
        ctx.advisories.append(
            f"[GATE FAILURE] Security gate {gate_name} crashed ({exc!r}). This edit is allowed "
            f"because it targets GRIP's own gate code — repair it, then normal enforcement resumes."
        )
        return None

    # The repair set is rendered from GATE_FAILURE_REPAIR_DIRS rather than
    # retyped, so widening the tuple can never again leave the session reading a
    # narrower promise than the code enforces.
    repair = " or ".join(f"{CLAUDE_DIR}/{rel}" for rel in GATE_FAILURE_REPAIR_DIRS)
    return deny(
        f"[GUARDRAIL: GATE FAILURE] Security gate {gate_name} raised {exc!r} and could not "
        f"return a verdict, so {ctx.tool_name} is denied rather than silently allowed. "
        f"Read/Grep/Glob, AskUserQuestion, and edits under {repair} stay open so you can fix it. "
        f"Details: logs/hooks.log and data/gate-failures.jsonl."
    )


def _run_pipeline(ctx: GateContext) -> dict | None:
    """Run every gate, isolating failures. Returns the terminating response, or None."""
    for gate_fn in GATE_PIPELINE:
        try:
            result = gate_fn(ctx)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — a crash must not become a silent allow
            # SystemExit is caught too: a gate that calls sys.exit() would
            # otherwise reproduce the exact no-stdout/non-zero-exit fail-open
            # this wrapper exists to close.
            result = _handle_gate_failure(gate_fn, exc, ctx)
        if result is not None:
            return result
    return None


def _extract_target(input_data: dict) -> str:
    """Extract evaluation target from tool input."""
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    if tool_name in ("Read", "Glob", "Grep"):
        return tool_input.get("file_path") or tool_input.get("path") or tool_input.get("pattern", "")
    elif tool_name == "Bash":
        return tool_input.get("command", "")
    elif tool_name in ("Write", "Edit"):
        return tool_input.get("file_path", "")
    return ""


def _emit_final_allow(ctx: GateContext):
    """Emit final allow with collected advisory messages and BAR routing."""
    log_gate_metric("all-passed", "allow")
    if AUDIT_ENABLED:
        log_tool_call(ctx.session_id, ctx.tool_name, ctx.tool_input, "allow", ctx.transcript_path)

    # BAR routing advisory (advisory only — never blocks)
    if ctx.tool_name in ("Bash", "Write", "Edit", "Task"):
        try:
            sys.path.insert(0, str(_HOOK_TREE / "lib"))
            from prompt_router import get_routing_advisory
            bar_input = ctx.tool_input.get("command", "") if isinstance(ctx.tool_input, dict) else str(ctx.tool_input)
            bar_advisory = get_routing_advisory(ctx.tool_name, bar_input)
            if bar_advisory:
                ctx.advisories.append(bar_advisory)
        except Exception:
            pass

    final_message = " ".join(ctx.advisories) if ctx.advisories else None

    # Fractal gate enforcement: inject gate awareness into subagent context
    gate_context = None
    if ctx.agent_id:
        try:
            from gate_rules import COMPACT_GATE_TEXT
            gate_context = COMPACT_GATE_TEXT
        except ImportError:
            gate_context = (
                "GRIP gate active. No dependency folders, no redundant reads, "
                "use dedicated tools, CC<30, DRY, KISS."
            )

    print(json.dumps(allow(final_message, additional_context=gate_context)))


def _read_payload() -> dict | None:
    """Parse stdin into a gate payload, or emit an allow and return None.

    Both failure branches ALLOW: with no tool_name there is nothing to
    adjudicate, and denying every call on a malformed payload is the deadlock
    class rules/gate-resolution-escape-hatch.md forbids.

    The isinstance check is the point. A payload that is valid JSON but not an
    object (`[]`, `"x"`, `3`, `null`) used to reach GateContext(...) and raise
    AttributeError BEFORE _run_pipeline could isolate it — exit 1 with an empty
    stdout, which the harness ignores as an ALLOW. That is the silent fail-open
    the isolation wrapper exists to close, reachable three lines above it.
    Reproduced with `echo '[]' | venv/bin/python hooks/context-gate-hook.py`.
    It still allows, but LOUDLY.
    """
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps(allow()))
        return None
    if not isinstance(input_data, dict):
        log_hook("ERROR", f"Payload is {type(input_data).__name__}, not an object — allowing, ungated")
        print(json.dumps(allow(
            "[GATE FAILURE] The hook payload was not a JSON object, so no gate could be "
            "evaluated. This call was allowed UNGATED. See logs/hooks.log."
        )))
        return None
    return input_data


def main():
    """Coordinator: parse input, run gate pipeline, emit result."""
    input_data = _read_payload()
    if input_data is None:
        return

    ctx = GateContext(
        tool_name=input_data.get("tool_name", ""),
        tool_input=input_data.get("tool_input", {}),
        session_id=input_data.get("session_id", ""),
        transcript_path=input_data.get("transcript_path", ""),
        target=_extract_target(input_data),
        agent_id=input_data.get("agent_id", ""),
        agent_type=input_data.get("agent_type", ""), cwd=input_data.get("cwd") or "",
    )

    log_hook("INFO", f"Tool: {ctx.tool_name}")
    _eval_ctx.update({
        "tool_name": ctx.tool_name,
        "session_id": ctx.session_id,
        "target": ctx.target,
        "transcript_path": ctx.transcript_path,
    })

    # Run gate pipeline. Failures are isolated per gate (see _run_pipeline):
    # a crashed SECURITY gate denies, a crashed ADVISORY gate continues loudly.
    result = _run_pipeline(ctx)
    if result is not None:
        print(json.dumps(result))
        return

    # All gates passed
    _emit_final_allow(ctx)


if __name__ == "__main__":
    main()
