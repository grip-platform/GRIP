"""Context gate orchestration -- extracted from context-gate-hook.py (#933).

Handles token estimation, critical/warning thresholds, and soft checkpoints.
Reduces main() by ~170 lines of threshold logic.
"""

import os
import re
import subprocess
import time
from pathlib import Path

from .context_gate import (
    estimate_tokens_from_jsonl,
    get_state,
    save_state,
    get_session_state,
    save_session_state,
    advisory_band,
    check_soft_checkpoints,
    _get_context_gate_summary,
    _read_cc_native_context,
    CONTEXT_MAX,
    CONTEXT_IS_EXTENDED,
    CONTEXT_CRITICAL,
    CONTEXT_WARNING,
)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR_OVERRIDE", str(Path.home() / ".claude")))


def _grip_python() -> str:
    """The venv interpreter, for spawns that sign a record.

    Resolved lazily and defensively: this module is imported by a PreToolUse
    gate, so an import failure here would take the gate down rather than the
    save. `grip_python` falls back to `sys.executable`, which by construction
    runs -- so the worst case is the status quo ante, never a crash.
    """
    import sys as _sys
    _lib = str(Path(__file__).resolve().parent.parent)
    if _lib not in _sys.path:
        _sys.path.insert(0, _lib)
    try:
        from _compat import grip_python
        return grip_python(CLAUDE_DIR)
    except Exception:  # noqa: BLE001 -- a gate must never die resolving a path
        return _sys.executable


def _resolve_usage(transcript_path: str, session_id: str | None = None) -> tuple:
    """Return (estimated_tokens, usage_percent) using CC native if fresh, else JSONL.

    session_id scopes the CC-native sidecar lookup to THIS session's file,
    preventing a parallel session's pct from tripping this session's gate.
    """
    native = _read_cc_native_context(max_age=120, session_id=session_id)
    if native and native.get("pct") is not None:
        pct = float(native["pct"])
        return int(pct * CONTEXT_MAX / 100), pct
    tokens = estimate_tokens_from_jsonl(transcript_path)
    return tokens, (tokens / CONTEXT_MAX) * 100


def run_context_gate(transcript_path, session_id, tool_name, tool_input,
                     *, autonomous_mode, context_is_extended,
                     log_hook_fn, log_gate_metric_fn,
                     deny_fn, allow_fn, is_subagent=False):
    """Critical (85%): autonomous/sub-agent→observe, interactive→HALT. is_subagent
    (agent_id present) ⇒ never HALT (inherits parent %, can't /compact). Default unchanged."""
    estimated_tokens, usage_percent = _resolve_usage(transcript_path, session_id=session_id)

    state = get_state()
    if state.get("session_id") != session_id:
        state = {"warning_issued": False, "session_id": session_id, "triggered_checkpoints": []}

    # Always update usage metrics for statusline consumption
    state["usage_percent"] = round(usage_percent, 1)
    state["estimated_tokens"] = estimated_tokens
    state["context_max"] = CONTEXT_MAX
    save_state(state)

    # CRITICAL threshold (85%) — autonomous: AUTO-COMPACT directive; interactive: HALT+ask.
    # Check BEFORE soft checkpoints so the critical-tier response takes precedence.
    if estimated_tokens >= CONTEXT_CRITICAL:
        result = _handle_critical(
            estimated_tokens, usage_percent, state, session_id,
            tool_name, tool_input, autonomous_mode, context_is_extended,
            log_hook_fn, log_gate_metric_fn, deny_fn, allow_fn,
            is_subagent=is_subagent,
        )
        if result is not None:
            return result

    # Below critical: warning (50%) + soft checkpoints (non-blocking saves).
    return _handle_below_critical(
        estimated_tokens, usage_percent, session_id,
        autonomous_mode, context_is_extended, log_gate_metric_fn, allow_fn,
    )


def _handle_below_critical(estimated_tokens, usage_percent, session_id,
                           autonomous_mode, context_is_extended,
                           log_gate_metric_fn, allow_fn):
    """Below-critical advisory: speak once per band per session, then stay quiet.

    This is an ADVISORY, not a block -- it has never denied a tool call and still
    does not. All that changes here is how often it speaks. The blocking-threshold
    path (`_handle_critical`) is untouched.

    The advisory repeats only when it has something new to say: the session has
    entered a materially higher band (50 -> 60 -> 70 -> 80 by default). Crossing
    65 -> 66 is not news, and re-announcing it on every tool call is a warning
    nobody reads. The band watermark lives in per-session state, so a second live
    session can no longer reset it (see get_session_state for why that mattered).
    """
    sess = get_session_state(session_id)

    if estimated_tokens >= CONTEXT_WARNING:
        band = advisory_band(usage_percent)
        if band > sess.get("announced_band", -1):
            sess["announced_band"] = band
            # The background checkpoint save stays a once-per-session event, exactly
            # as before -- only the message repeats per band. Later bands must not
            # start spawning extra save processes.
            first_crossing = not sess.get("warning_saved")
            sess["warning_saved"] = True
            save_session_state(session_id, sess)
            log_gate_metric_fn("context-gate", "warn", f"{usage_percent:.0f}%")
            return _handle_warning(
                estimated_tokens, usage_percent, autonomous_mode,
                context_is_extended, allow_fn, do_save=first_crossing,
            )

    soft_result = check_soft_checkpoints(
        estimated_tokens, sess, session_id,
        save_fn=lambda s: save_session_state(session_id, s),
    )
    if soft_result:
        log_gate_metric_fn("soft-checkpoint", "allow")
        return soft_result

    return None


def _memory_md_exemption(tool_name, tool_input, usage_percent, log_hook_fn, allow_fn):
    """Allow MEMORY.md writes at the critical threshold so consolidation stays atomic."""
    if tool_name != "Edit":
        return None
    if "/memory/MEMORY.md" not in tool_input.get("file_path", ""):
        return None
    log_hook_fn("ALLOW", f"Memory consolidation exemption at {usage_percent:.0f}%")
    return allow_fn(f"[CONTEXT GATE - {usage_percent:.0f}%] Memory write allowed (consolidation protection).")


def _autonomous_cleanup_exemption(state, autonomous_mode, tool_name, tool_input,
                                   usage_percent, log_hook_fn, allow_fn):
    """Narrow allowlist for git/read ops inside the 2-minute autonomous cleanup window."""
    if not (autonomous_mode and state.get("autonomous_cleanup")):
        return None
    elapsed = time.time() - state.get("cleanup_started", 0)
    if elapsed > 120:
        return None
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if re.search(r'^git\s+(commit|push|status|add|log|diff)', cmd):
            log_hook_fn("ALLOW", f"Autonomous cleanup: {cmd[:60]}")
            return allow_fn(f"[CONTEXT GATE - CLEANUP] {usage_percent:.0f}% | Cleanup window: {120 - int(elapsed)}s remaining. Commit, push, then end session.")
        return None
    if tool_name in ("Read", "Grep", "Glob"):
        log_hook_fn("ALLOW", f"Autonomous cleanup read: {tool_name}")
        return allow_fn(f"[CONTEXT GATE - CLEANUP] {usage_percent:.0f}% | Read allowed during cleanup window.")
    return None


def _spawn_continuation_prompt():
    """Fire-and-forget background spawn of the continuation prompt generator (#931)."""
    try:
        subprocess.Popen(
            ["python3", str(CLAUDE_DIR / "lib" / "continuation_prompt.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # Continuation prompt failure must not block gate


def _kono_autostore(usage_percent, estimated_tokens):
    """Store a context-gate marker in KONO before the gate blocks (Issue #76). Never raises."""
    try:
        _venv_py = str(Path.home() / ".claude" / "venv" / "bin" / "python3")
        _kono_script = str(CLAUDE_DIR / "lib" / "kono_substrate.py")
        if Path(_venv_py).exists():
            subprocess.run(
                [_venv_py, _kono_script, "store",
                 "--content", f"Context gate at {usage_percent:.0f}% ({estimated_tokens:,} tokens)"],
                capture_output=True, timeout=5,
            )
    except Exception:
        pass  # KONO failure must never block context gate


def _defensive_checkpoint(usage_percent, log_hook_fn):
    """Lightweight instance-serializer defensive checkpoint (fail-open) so GRIP state
    survives any unforeseen failure of CC's native auto-compact."""
    try:
        subprocess.run(
            ["python3", str(CLAUDE_DIR / "lib" / "instance-serializer.py"),
             "auto", "--achievement",
             f"Defensive checkpoint at {usage_percent:.0f}% context"],
            capture_output=True, timeout=10,
        )
    except Exception as _e:
        log_hook_fn("ERROR", f"Defensive checkpoint failed: {_e}")


def _maybe_cold_tier_offload(usage_percent, log_hook_fn):
    """OPT-IN cold-tier offload (Deliverable B live-gate wiring), FAIL-OPEN.

    Runs as part of the defensive checkpoint on the autonomous-observe path. Default
    no-op (no slice provider wired); offloads GRIP cold state only when the operator
    opted in (auto-compact-config enabled AND context >= threshold) AND a provider +
    pager are wired. Can NEVER block or alter the gate's allow — every error is
    swallowed. The critical path is observe-only (no HALT) since 2026-06-08.
    """
    try:
        try:
            from cold_compaction_gate import offload_for_autonomous_compaction
        except ModuleNotFoundError:  # PYTHONPATH=$HOME/.claude package root
            from lib.cold_compaction_gate import offload_for_autonomous_compaction
        _offload = offload_for_autonomous_compaction(usage_percent)
        if _offload.get("offloaded"):
            log_hook_fn("OBSERVE", f"Cold-tier offload: {_offload['offloaded']} slice(s) at {usage_percent:.0f}%")
    except Exception as _e:
        # #3394: OBSERVE, not ERROR — the offload is fail-open by contract on
        # the autonomous path; a missing optional module is expected, not an
        # incident. Still logged (never silent) per the observability doctrine.
        log_hook_fn("OBSERVE", f"Cold-tier offload skipped: {_e}")


def _observe_critical_threshold(estimated_tokens, usage_percent, state, summary_block, allow_fn, log_hook_fn):
    """Passive observability at the CRITICAL threshold — TRUST CC native autoCompact.

    InfiniteMachine done correctly: the SUBSTRATE auto-compacts, not the operator and
    not the agent. Claude Code's settings.json has ``autoCompact: true`` enabled, which
    triggers native compaction near the model's context limit. The PreCompact hook
    (``~/.hal/hooks/pre-compact-hook.py``) automatically captures GRIP state into the
    compaction summary. Zero operator action, zero agent action — V>>'s 2026-05-12
    mandate: "auto compact must happen — no need for a user to run /compact themselves".

    This function therefore:
    - Does NOT emit a /compact directive (would offload action onto the agent).
    - Does NOT block the tool call (autonomous loop continues normally).
    - Runs the lightweight ``instance-serializer`` defensive checkpoint so state
      survives any unforeseen failure of CC's native auto-compact.
    - Emits a passive observability allow message — operator/statusline can see
      the pressure point but no action is required.

    History:
    - Original ``_deny_autonomous``: blocked + told the agent to end the session.
      Killed autonomous loops at the gate's threshold. (Pre-2026-05-12.)
    - ``_allow_compact_continue``: allow + agent-facing /compact directive.
      Still placed the action burden on the agent. (PR #2656.)
    - ``_observe_critical_threshold``: passive observe + trust CC native autoCompact.
      No agent burden, no operator burden. (This change.)

    Falsification: this is wrong if CC's native autoCompact fails to fire before the
    session crashes from context overflow. Track via PreCompact hook execution counts
    in ``logs/sentinel.jsonl`` versus context-gate ``observe`` counts in the same
    window — they should correlate (every observe should be followed by a PreCompact
    within ~10-20% more context usage).
    """
    _defensive_checkpoint(usage_percent, log_hook_fn)
    _maybe_cold_tier_offload(usage_percent, log_hook_fn)

    return allow_fn(
        f"[CONTEXT GATE - {usage_percent:.0f}%] {estimated_tokens:,}/{CONTEXT_MAX:,} "
        f"tokens. State checkpointed.{summary_block} "
        f"CC native autoCompact handles compaction automatically near the model's "
        f"context limit (PreCompact hook captures GRIP state). No action required."
    )


def _handle_critical(estimated_tokens, usage_percent, state, session_id,
                     tool_name, tool_input, autonomous_mode, context_is_extended,
                     log_hook_fn, log_gate_metric_fn, deny_fn, allow_fn,
                     is_subagent=False):
    """Handle critical threshold (85%). Returns response or None for exemptions."""
    mem_exempt = _memory_md_exemption(tool_name, tool_input, usage_percent, log_hook_fn, allow_fn)
    if mem_exempt:
        return mem_exempt

    cleanup_exempt = _autonomous_cleanup_exemption(
        state, autonomous_mode, tool_name, tool_input, usage_percent, log_hook_fn, allow_fn
    )
    if cleanup_exempt:
        return cleanup_exempt

    # Sub-agent: NEVER HALT (reads the PARENT's context %, has no operator,
    # can't /compact itself). Full rationale in _observe_subagent.
    if is_subagent:
        return _observe_subagent(estimated_tokens, usage_percent,
                                 log_hook_fn, log_gate_metric_fn, allow_fn)

    _spawn_continuation_prompt()
    _kono_autostore(usage_percent, estimated_tokens)

    ctx_summary = _get_context_gate_summary()
    summary_block = f" STATE: {ctx_summary}" if ctx_summary else ""

    # NEVER HALT — OBSERVE-and-continue at the critical threshold for BOTH
    # autonomous AND interactive sessions (V>> 2026-06-08 mandate: "DO NOT HALT AT
    # 85% — CONTINUE"; ref PR #1959 InfiniteMachine auto-compact-never-halt). The
    # substrate auto-compacts (CC native autoCompact + PreCompact hook) and a
    # defensive checkpoint + cold-tier offload preserve GRIP state. The old
    # interactive HALT-and-ask (Rule 8) is RETIRED here: it deadlocked live sessions
    # and the AskUserQuestion fallback was itself gate-blocked. The proper, lossless
    # relief valve is the ultraclear→/clear marker-watcher, never a deny. ``deny_fn``
    # is retained in the public signature for hook API parity but is never called at
    # the critical threshold — the gate observes-and-continues in every mode.
    log_hook_fn("OBSERVE", f"Context gate critical observe "
                f"({'autonomous' if autonomous_mode else 'interactive'}): {usage_percent:.0f}%")
    log_gate_metric_fn("context-gate", "observe", f"{usage_percent:.0f}%")
    return _observe_critical_threshold(estimated_tokens, usage_percent, state, summary_block, allow_fn, log_hook_fn)


def _observe_subagent(estimated_tokens, usage_percent, log_hook_fn, log_gate_metric_fn, allow_fn):
    """Passive observe for a dispatched sub-agent at the critical threshold — never HALT.

    A sub-agent's PreToolUse hook reads the PARENT session's context % (sub-agents
    share the parent session_id, so _resolve_usage hits the parent's per-session
    sidecar — verified 2026-05-29 across 392 agent-*.jsonl, every one isSidechain
    carrying the parent's sessionId). The sub-agent has no operator to answer an
    interactive HALT and cannot /compact itself, so an interactive HALT would deadlock
    the dispatch at its FIRST tool call — the #3291 false-block, and a mechanical cause
    of the dispatch-incomplete symptom (rules/agent-dispatch-protocol.md) that NO
    prompt fix (atomic-deliverable / anti-pause) can reach. Parent-session
    checkpoint machinery (continuation prompt / KONO / instance-serializer) is a
    PARENT concern, skipped here. Worst case is bounded + milder than today's
    failure: the sub-agent overflows its OWN window and the harness ends it, versus
    blocking every sub-agent at iteration 0. The parent's own gate governs the parent.
    """
    log_hook_fn("OBSERVE", f"Sub-agent context observe (parent {usage_percent:.0f}%) — sub-agents are not HALTed")
    log_gate_metric_fn("context-gate", "observe-subagent", f"{usage_percent:.0f}%")
    return allow_fn(
        f"[CONTEXT GATE - SUB-AGENT] Parent session at {usage_percent:.0f}% "
        f"({estimated_tokens:,}/{CONTEXT_MAX:,}). Sub-agents are not context-HALTed — "
        f"the parent session owns compaction. Proceed; your own context window is "
        f"separate and bounded."
    )


def _handle_warning(estimated_tokens, usage_percent, autonomous_mode,
                    context_is_extended, allow_fn, do_save=True):
    """Handle warning threshold (50%). Always returns an allow with message.

    `do_save` gates only the background checkpoint, so the once-per-session save
    is preserved while the message may repeat on a later band crossing.
    """
    # Auto-save at 50% (non-blocking background save via grip_save.py)
    if do_save:
        try:
            subprocess.Popen(
                [_grip_python(), str(CLAUDE_DIR / "lib" / "grip_save.py"),
                 f"Auto-checkpoint at {usage_percent:.0f}% context", "--quiet"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass  # Save failure must not block the gate

    return allow_fn(
        f"[CONTEXT GATE - {usage_percent:.0f}%] "
        f"Approaching limit ({estimated_tokens:,}/{CONTEXT_MAX:,}). "
        f"Consider /compact or session transition."
    )
