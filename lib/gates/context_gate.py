"""Context Gate - Token estimation, state management, checkpoint logic.

Extracted from hooks/context-gate-hook.py (#933).
Continues the pattern established by bash_gates.py, git_gates.py, security_gates.py.
"""

import json
import math
import os
import subprocess
import time
from pathlib import Path

# Configuration - Dynamic thresholds via ACE (Adaptive Context Extension)
# Dual-context import (mirrors evaluator.py / AGORA #3466): hooks import this
# package by BARE name with only ~/.claude/lib on sys.path (script mode — no
# cwd entry), where the `lib.` package root does not exist. The bare fallback
# keeps the WHOLE gates package importable there; without it, gates/__init__
# dies at this line and every dependent hook silently loses its yolo bypass
# (the 2026-07-13 yolo_quality deadlock — gate-resolution-escape-hatch.md).
try:
    from lib.state_manager import load_state
    from lib.atomic_write import atomic_write_json
    from lib.grip_tier import configured_assumed_window as _tier_assumed_window
except ImportError:  # imported with ~/.claude/lib (not ~/.claude) on path
    from state_manager import load_state
    from atomic_write import atomic_write_json
    from grip_tier import configured_assumed_window as _tier_assumed_window


def get_context_limits() -> tuple:
    """Get context limits, in order of precedence.

    1. Env var CONTEXT_GATE_MAX — always wins (backward compat).
    2. The model-state file's own context_window, when model-state-manager.py
       has actually recorded one (set by model-state-manager.py).
    3. The plan-tier envelope's assumed_window (lib/grip_tier.py) — tier from
       GRIP_TIER env, else the persisted tier config, NEVER
       grip_tier.detect_tier(). detect_tier() shells out to the macOS
       `security` CLI, and this function re-resolves on EVERY PreToolUse
       tool call — a subprocess spawn in that hot path is a per-call cost
       and a platform dependency this gate must never carry. Without this
       step, a Claude Pro session (only a 200K window) was assumed to have
       1,000,000, so a 30% soft checkpoint fired at 300K instead of 60K.
    4. 1,000,000 — the historical default (API/pay-as-you-go, or a tier that
       is unset / unresolvable without detection).

    Returns (context_max, is_extended) tuple.
    """
    # 1. Env var override takes highest precedence.
    env_max = os.environ.get("CONTEXT_GATE_MAX")
    if env_max:
        return int(env_max), False

    # 2. Model state, when model-state-manager.py actually recorded a window.
    try:
        state = load_state("model-state")
        if state and state.get("context_window"):
            return state["context_window"], state.get("extended", False)
    except Exception:
        pass

    # 3. The tier envelope — never via detection (see docstring point 3).
    try:
        assumed = _tier_assumed_window()
        if assumed:
            return assumed, False
    except Exception:
        pass

    # 4. Final fallback: the full 1M window.
    return 1000000, False


_ctx_max, _is_extended = get_context_limits()
CONTEXT_MAX = _ctx_max
CONTEXT_IS_EXTENDED = _is_extended
# Defaults: WARNING 50% (early background checkpoint), CRITICAL 85% (auto-compact threshold
# per Rule 8 in rules/efficiency-rules.md). The prior 70% default caused premature autonomous-
# loop termination at 74% on 1M Opus contexts — V>> 2026-05-12: "for autonomous loops this is
# the ultimate failure mode". Raised to 85% to maximise InfiniteMachine runway before /compact
# is signalled. grip-hal overrides these per provider via the same env vars.
CONTEXT_WARNING = int(os.environ.get("CONTEXT_GATE_WARNING", int(CONTEXT_MAX * 0.50)))
CONTEXT_CRITICAL = int(os.environ.get("CONTEXT_GATE_CRITICAL", int(CONTEXT_MAX * 0.85)))


def _env_int(name: str, default: int) -> int:
    """Read a positive int from the environment, falling back on anything unparseable.

    Deliberately total: this module is imported by a PreToolUse hook that runs on
    EVERY tool call, so a malformed env var must never raise at import time.
    """
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


# Width, in percentage points, of an advisory band. The below-critical advisory
# speaks at most ONCE per band per session: crossing 50 -> 60 -> 70 -> 80 is news,
# 65 -> 66 is not. Before this the advisory carried a warn-once boolean that could
# not hold across concurrent sessions (see get_session_state), so the identical
# sentence was re-emitted on essentially every tool call.
CONTEXT_ADVISORY_BAND = _env_int("CONTEXT_GATE_ADVISORY_BAND", 10)

# Soft checkpoint thresholds (background save, non-blocking).
# Scaled to 50/85 warning/critical: checkpoints at 30/45/60 keep the gradient intact and
# fire well below the auto-compact threshold so state is preserved if a compact is missed.
SOFT_CHECKPOINTS = [20, 40, 60] if CONTEXT_IS_EXTENDED else [30, 45, 60]

# State files for tracking
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR_OVERRIDE", str(Path.home() / ".claude")))
STATE_FILE = CLAUDE_DIR / "state" / "features" / "context-gate-state.json"

# Sidecar written by statusline-command.sh with CC's authoritative context percentage.
# More accurate than JSONL scan: includes system-prompt injection, survives compaction.
_NATIVE_CONTEXT_FILE = CLAUDE_DIR / "state" / "features" / "cc-native-context.json"

# Token estimation cache: avoid re-parsing JSONL on every tool call
_token_cache = {"mtime": 0.0, "size": 0, "estimate": 0}

# Logging function - set by hook at import time via configure_logging()
_log_hook = None


def _per_session_native_path(session_id: str) -> Path:
    return _NATIVE_CONTEXT_FILE.with_name(f"cc-native-context-{session_id}.json")


def _load_native_payload(path: Path, max_age: int) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    if time.time() - data.get("ts", 0) >= max_age:
        return None
    native_max = int(data.get("context_max", 0))
    if native_max > 0:
        ratio = max(native_max, CONTEXT_MAX) / min(native_max, CONTEXT_MAX)
        if ratio > 2.0:
            return None  # HAL provider mismatch — fall back to JSONL estimate
    return data


def _read_cc_native_context(
    max_age: int = 300, session_id: str | None = None
) -> dict | None:
    """Return CC-native context data written by statusline for THIS session.

    The statusline receives context_window.used_percentage directly from CC's
    in-memory state (accurate, includes system-prompt injection, survives compaction).
    This is the authoritative source — always prefer it over the JSONL estimate.

    Per-session isolation: when session_id is provided, prefer
    `cc-native-context-{session_id}.json`. The legacy global path is consulted
    only as a fallback AND only if its embedded session_id matches the caller's,
    so a parallel session's pct can never trip this session's gate.
    """
    if session_id:
        data = _load_native_payload(_per_session_native_path(session_id), max_age)
        if data is not None:
            return data
    legacy = _load_native_payload(_NATIVE_CONTEXT_FILE, max_age)
    if legacy is None:
        return None
    legacy_session = legacy.get("session_id")
    if session_id and legacy_session and legacy_session != session_id:
        return None  # Cross-session contamination guard
    return legacy


def configure_logging(log_fn):
    """Set the logging function used by this module.

    Called by the hook after import to wire up log_hook.
    """
    global _log_hook
    _log_hook = log_fn


def _log(level: str, message: str):
    """Internal log helper that delegates to configured log_hook."""
    if _log_hook:
        _log_hook(level, message)


_MEMORY_STATUSES = ("PENDING", "BUG", "IN PR", "IN PROGRESS", "IN ISSUE")


def _content_chars(content) -> int:
    """Char length of a message content field (str or list of text blocks)."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(b.get("text", "")) for b in content if isinstance(b, dict))
    return 0


def _extract_entry_chars(entry: dict) -> int:
    """Sum content + tool_input + tool_output char lengths for one JSONL entry."""
    total = 0
    msg = entry.get("message")
    if isinstance(msg, dict):
        total += _content_chars(msg.get("content", ""))
    if "tool_input" in entry:
        total += len(json.dumps(entry["tool_input"]))
    if "tool_output" in entry:
        total += len(str(entry.get("tool_output", "")))
    return total


def _extract_usage_tokens(entry: dict) -> int | None:
    """Extract API-reported total input tokens from a JSONL entry.

    Claude Code writes the API's usage payload to each assistant turn's
    ``message.usage`` field. The real context load per turn is the sum of:
      - input_tokens              (fresh input, typically small)
      - cache_read_input_tokens   (replayed from prompt cache — the bulk)
      - cache_creation_input_tokens (newly cached segments)

    Returns None when the entry has no usage data.
    """
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    total = (
        usage.get("input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
    )
    return total if total > 0 else None


def _is_compaction_boundary(entry: dict) -> bool:
    """True if this JSONL entry is Claude Code's compaction-boundary marker.

    Verified against live transcripts (~/.claude/projects/*/*.jsonl, 2026-06-02):
    the harness writes a ``{"type": "system", "subtype": "compact_boundary",
    "compactMetadata": {...}}`` system entry at each ``/compact``. After it, the
    on-disk JSONL still retains every pre-compaction line, but the IN-MEMORY
    context is now small (``compactMetadata.postTokens``, ~20k). Any
    ``message.usage`` recorded BEFORE this marker reflects the discarded
    pre-compaction context and must not be treated as live (see
    ``_latest_usage_tokens``).
    """
    return entry.get("type") == "system" and entry.get("subtype") == "compact_boundary"


def _boundary_post_tokens(entry: dict) -> int | None:
    """Authoritative post-compaction context size from a boundary entry, or None.

    ``compactMetadata.postTokens`` is the harness's own count of the context
    retained after the compaction (~20k in practice) — strictly better than a
    char estimate. Returns None when the field is absent or non-positive, so the
    caller degrades gracefully to the char heuristic (a future boundary variant
    without ``postTokens`` never raises).
    """
    meta = entry.get("compactMetadata")
    if not isinstance(meta, dict):
        return None
    post = meta.get("postTokens")
    # bool is an int subclass — exclude it; reject NaN/inf so int() can't raise.
    if isinstance(post, bool) or not isinstance(post, (int, float)):
        return None
    if not math.isfinite(post) or post <= 0:
        return None
    # Sanity ceiling: a post-compaction size cannot exceed the context window.
    # An implausible value (corrupt/garbage) degrades to the char heuristic
    # rather than feeding a bogus token count straight into the gate.
    if post > CONTEXT_MAX:
        return None
    return int(post)


def _latest_usage_tokens(path: str) -> int | None:
    """Return the most recent turn's total input tokens, or None.

    Reverse-scans JSONL to find the last entry with usage data. The returned
    value is the API-authoritative per-turn context size — NOT a sum across
    turns, because each assistant turn re-sends the full history.

    Compaction-aware: the scan STOPS at the last ``compact_boundary`` marker.
    A ``message.usage`` from before that boundary is stale (it describes the
    context the harness already discarded). If the reverse-scan reaches the
    boundary before finding any post-boundary usage, it returns the boundary's
    own ``compactMetadata.postTokens`` (the harness-authoritative post-compaction
    size) when present, else None so the caller falls back to a char estimate
    over post-boundary content only — either way the gate never sees the stale
    pre-compaction number. Transcripts with no boundary are scanned to the start
    exactly as before (byte-identical behaviour).
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except (OSError, UnicodeError):
        return None  # read/decode failure → fail-safe; caller falls back to char/0
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if _is_compaction_boundary(entry):
            # No live usage after the last compaction. Prefer the harness's own
            # authoritative postTokens; None falls through to the char heuristic.
            return _boundary_post_tokens(entry)
        tokens = _extract_usage_tokens(entry)
        if tokens is not None:
            return tokens
    return None


def _sum_jsonl_content_chars(path: str) -> int:
    """Sum char content, counting only entries AFTER the last compaction boundary.

    Forward-scan: a ``compact_boundary`` marker resets the accumulator, so when
    the JSONL still holds pre-compaction lines on disk the heuristic measures
    only the small post-compaction slice — never the discarded history. With no
    boundary present this sums the whole file exactly as before.
    """
    total = 0
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if _is_compaction_boundary(entry):
                    total = 0  # discard pre-compaction content; count post-boundary only
                    continue
                total += _extract_entry_chars(entry)
    except (OSError, UnicodeError):
        return 0  # read/decode failure → fail-safe 0 (estimate_tokens: unknown)
    return total


def estimate_tokens_from_jsonl(transcript_path: str) -> int:
    """Estimate token count from conversation JSONL file.

    Prefers API-reported usage counts when available (authoritative), falling
    back to a char-based heuristic for transcripts without usage data.

    Why the fallback matters: the chars/4 heuristic severely undercounts
    because it misses the re-injected system prompt, tool-result blocks, and
    prompt-cache replays that make up the bulk of each turn. A session
    measured at 508k tokens by the API surfaced as 30k chars/4 via the
    heuristic — a 17× undercount that silenced the soft-checkpoint gates.

    Performance optimization (#165): caches result keyed on file mtime+size.
    Only re-parses the JSONL when the transcript has actually changed.
    """
    if not transcript_path or not Path(transcript_path).exists():
        return 0
    try:
        st = os.stat(transcript_path)
        cur_mtime, cur_size = st.st_mtime, st.st_size
    except OSError:
        return 0
    if cur_mtime == _token_cache["mtime"] and cur_size == _token_cache["size"]:
        return _token_cache["estimate"]

    # PRIMARY: API-reported usage (authoritative)
    usage_tokens = _latest_usage_tokens(transcript_path)
    if usage_tokens is not None:
        estimate = usage_tokens
    else:
        # FALLBACK: char-based heuristic for transcripts without usage data
        estimate = _sum_jsonl_content_chars(transcript_path) // 4

    _token_cache["mtime"] = cur_mtime
    _token_cache["size"] = cur_size
    _token_cache["estimate"] = estimate
    return estimate


def get_state() -> dict:
    """Load state from file."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"warning_issued": False, "session_id": None, "triggered_checkpoints": []}


def save_state(state: dict):
    """Save state to file ATOMICALLY (unique tmp + os.replace via atomic_write_json).

    A bare write_text truncates the file mid-write; the gate runs as a PreToolUse
    hook on EVERY tool call across all 5+ sessions, so a concurrent get_state() in
    another session could read the truncated file, hit the except, and fall back to
    the DEFAULT — silently resetting warning_issued / triggered_checkpoints. Atomic
    write removes that window. Same data, same gate behaviour; only the write is now
    atomic (AGORA #3462 #1, V>>-approved).
    """
    atomic_write_json(STATE_FILE, state)


def advisory_band(usage_percent: float) -> int:
    """Lower edge of the advisory band containing usage_percent (10 -> 60, 65 -> 60)."""
    return int(usage_percent // CONTEXT_ADVISORY_BAND) * CONTEXT_ADVISORY_BAND


def _per_session_state_path(session_id: str | None) -> Path:
    """Sidecar path holding ONE session's announce state.

    Mirrors the `cc-native-context-{session_id}.json` convention already used in
    this directory.
    """
    raw = str(session_id or "default")
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in raw)[:120] or "default"
    return STATE_FILE.with_name(f"context-gate-state-{safe}.json")


def get_session_state(session_id: str | None) -> dict:
    """Load the announce state belonging to THIS session only.

    Why this exists. The shared STATE_FILE is written by every concurrent session
    on every tool call, and the orchestrator resets its dedup flags whenever the
    file's `session_id` stamp does not match the caller. With two or more sessions
    live -- GRIP's normal operating state -- the sessions overwrite each other's
    stamp in turn, so each one finds a foreign stamp on its next call and resets
    the flags it had just set. The warn-once guard therefore never held, and the
    same advisory was re-announced on essentially every tool call.

    Keeping the announce state in a per-session file removes the shared mutable
    cell entirely: a session only ever reads and writes its own. That also closes
    a read-modify-write race the shared file had, since no two sessions write here.
    The shared STATE_FILE is still written unchanged for the statusline snapshot.
    """
    path = _per_session_state_path(session_id)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"announced_band": -1, "warning_saved": False, "triggered_checkpoints": []}


def save_session_state(session_id: str | None, state: dict) -> None:
    """Persist one session's announce state. Never raises -- the gate must not break."""
    try:
        state["session_id"] = session_id
        state["updated_at"] = time.time()
        atomic_write_json(_per_session_state_path(session_id), state)
    except Exception:
        pass


def get_context_utilisation_snapshot() -> dict:
    """Structured snapshot of current context utilisation for banner rendering.

    Reads the state cache that context-gate-hook writes on every tool call
    (the same file `get_state()` returns). Returns a dict with:

      - pct:           `usage_percent` rounded to 1 decimal (float)
      - tokens_used:   `estimated_tokens` (int)
      - tokens_budget: `context_max` (int, defaults to CONTEXT_MAX)
      - session_id:    the session that last wrote the cache (str, may be empty)

    Distinct from `_get_context_gate_summary()`, which returns a formatted
    memory + git string for the 85% deny message. This helper is for callers
    that need structured numeric data (e.g. the `/save-exit` banner).
    """
    # Prefer CC native (written by statusline) — accurate post-compaction, includes
    # system-prompt injection that JSONL scan underestimates by up to 5x.
    cached_session = get_state().get("session_id", "")
    native = _read_cc_native_context(max_age=300, session_id=cached_session)
    if native and native.get("pct") is not None:
        pct = round(float(native["pct"]), 1)
        budget = CONTEXT_MAX
        return {
            "pct": pct,
            "tokens_used": int(pct * budget / 100),
            "tokens_budget": budget,
            "session_id": cached_session,
        }

    state = get_state()
    pct = round(state.get("usage_percent", 0.0), 1)
    budget = state.get("context_max", CONTEXT_MAX)
    return {
        "pct": pct,
        "tokens_used": int(pct * budget / 100),
        "tokens_budget": budget,
        "session_id": state.get("session_id", ""),
    }


def _scan_line_for_memory_status(line: str) -> str | None:
    """Return the first matching MEMORY.md status found on this line, or None."""
    for status in _MEMORY_STATUSES:
        if f"| {status}" in line:
            return status
    return None


def _memory_pending_summary() -> str:
    """Extract pending items from the latest MEMORY.md. Empty string on none/error."""
    try:
        memory_paths = list(CLAUDE_DIR.glob("projects/*/memory/MEMORY.md"))
        if not memory_paths:
            return ""
        memory_path = max(memory_paths, key=lambda p: p.stat().st_mtime)
        pending = []
        for line in memory_path.read_text().split("\n"):
            status = _scan_line_for_memory_status(line)
            if not status:
                continue
            cols = line.split("|")
            if len(cols) >= 4:
                pending.append(f"#{cols[1].strip()} {cols[2].strip()[:60]} [{status}]")
        if not pending:
            return ""
        return f"MEMORY: {len(pending)} open items. Recent: {'; '.join(pending[-5:])}"
    except Exception as _e:
        _log("DEBUG", f"MEMORY.md summary failed: {_e}")
        return ""


def _git_cmd_out(*args) -> str:
    """Run a git command, return stripped stdout. Empty string on timeout/error."""
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=3
        ).stdout.strip()
    except Exception:
        return ""


def _git_state_summary() -> str:
    """Compact git branch + uncommitted count + last commit. Empty string on error."""
    try:
        git_log = _git_cmd_out("log", "--oneline", "-3")
        if not git_log:
            return ""
        git_status = _git_cmd_out("status", "--porcelain")
        branch = _git_cmd_out("branch", "--show-current")
        changes = len(git_status.split("\n")) if git_status else 0
        return f"GIT: branch={branch}, {changes} uncommitted. Last: {git_log.split(chr(10))[0]}"
    except Exception as _e:
        _log("DEBUG", f"Git context summary failed: {_e}")
        return ""


def _get_context_gate_summary() -> str:
    """Build memory + git summary for context gate deny output (TODO #101 Part C).

    Only called when context gate triggers (85%+), so performance is acceptable.
    Returns a compact string with pending TODOs and git state for user awareness.
    """
    parts = [p for p in (_memory_pending_summary(), _git_state_summary()) if p]
    return " | ".join(parts) if parts else ""


def check_soft_checkpoints(estimated_tokens: int, state: dict, session_id: str,
                           transcript_path: str = "", allow_fn=None,
                           save_fn=None) -> dict | None:
    """Check for soft checkpoint triggers. Non-blocking background saves.

    Args:
        estimated_tokens: Current estimated token count.
        state: Context gate state dict.
        session_id: Current session ID.
        transcript_path: Path to transcript JSONL file.
        allow_fn: The allow() function from the hook (for building responses).
        save_fn: Writer for `state`; defaults to the shared-file `save_state`.
            The orchestrator passes a per-session writer so `triggered_checkpoints`
            cannot be reset by another session -- without it, silencing the repeated
            advisory would simply expose the same repetition as re-fired checkpoints
            (each of which also spawns a background save process).
    """
    usage_percent = (estimated_tokens / CONTEXT_MAX) * 100

    triggered_checkpoints = state.get("triggered_checkpoints", [])

    for threshold in SOFT_CHECKPOINTS:
        if usage_percent >= threshold and threshold not in triggered_checkpoints:
            # Mark as triggered
            triggered_checkpoints.append(threshold)
            state["triggered_checkpoints"] = triggered_checkpoints
            (save_fn or save_state)(state)

            # Trigger background save via auto-memory-saver (Cortex pattern)
            try:
                cmd = [
                    "python3", str(CLAUDE_DIR / "lib" / "auto-memory-saver.py"),
                    session_id, "--force",
                ]
                if transcript_path:
                    cmd.extend(["--transcript", transcript_path])
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as _e:
                _log("ERROR", f"Soft checkpoint save failed: {_e}")

            _log("CHECKPOINT", f"Soft checkpoint triggered at {threshold}%")
            if allow_fn:
                return allow_fn(f"[SOFT CHECKPOINT {threshold}%] Background save triggered.")
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }, "systemMessage": f"[SOFT CHECKPOINT {threshold}%] Background save triggered."}

    return None
