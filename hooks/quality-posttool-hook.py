"""Quality Gate Hook — PostToolUse fast-path checks.

Detects quality violations AFTER Write/Edit completes.
Writes violation state for Two-Hook Gate enforcement (Gate V in PreToolUse).

Checks: DRY, KISS, BIG-O, YAGNI + syntax errors.
Version: 2.0.0 (Two-Hook Gate integration)
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from hook_utils import parse_hook_input, respond, extract_tool_info

from state_paths import FEATURE_QUALITY_DEBOUNCE, state_file

try:
    from quality_fast import ERROR
except ImportError:
    ERROR = "error"  # Fallback constant if quality_fast unavailable

# Under state/features/ since April audit task D5; it was a dotfile at the install root.
DEBOUNCE_FILE = state_file(FEATURE_QUALITY_DEBOUNCE)
DEBOUNCE_SECONDS = 3
ENFORCEMENT_DIR = Path.home() / ".claude" / "state" / "enforcement"


def _get_cc_pid():
    """Derive Claude Code PID (grandparent) for session-scoped state isolation."""
    try:
        ppid = os.getppid()
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(ppid)],
            capture_output=True, text=True, timeout=1,
        )
        cc_pid = result.stdout.strip()
        if cc_pid and cc_pid != "1":
            return cc_pid
    except Exception:
        pass
    return str(os.getpid())


def _path_key(file_path: str) -> str:
    """Stable 12-char hash key for a target file path (per-path violation scoping, #2837)."""
    return hashlib.sha256((file_path or "").encode("utf-8")).hexdigest()[:12]


def _violation_file_for_path(file_path: str):
    """Per-path violation state file (#2837: scoped per-target-path, not session-global).

    The legacy single-file design (`quality-violation-<PID>.json`) cascaded:
    a violation in worktree X blocked unrelated writes in worktree Y because
    only one violation slot existed. Per-path keying lets multiple paths each
    carry their own violation independently, and Gate V only blocks the
    Write/Edit whose target matches a stored violation's path.
    """
    return ENFORCEMENT_DIR / f"quality-violation-{_get_cc_pid()}-{_path_key(file_path)}.json"


def load_debounce():
    try:
        return json.loads(DEBOUNCE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_debounce(state):
    try:
        DEBOUNCE_FILE.write_text(json.dumps(state))
    except OSError:
        pass


def get_file_path(tool_name, tool_input):
    if tool_name == "Write":
        return tool_input.get("file_path", "")
    return tool_input.get("file_path", "")


def _get_content(tool_name, tool_input, file_path):
    """Extract file content from tool input or disk."""
    if tool_name == "Write":
        return tool_input.get("content", "")
    try:
        return Path(file_path).read_text(errors="ignore")
    except OSError:
        return ""


def _check_syntax(content, file_path):
    """Catch SyntaxError immediately after edit (#930)."""
    try:
        compile(content, file_path, "exec")
        return None
    except SyntaxError as e:
        return f"[SYNTAX ERROR] {file_path}:{e.lineno}: {e.msg}"


def _clear_violation_state_for_path(file_path: str):
    """Clear pending violation state for ONE path (Two-Hook Gate release).

    Per-path scoping (#2837) — clearing path A's violation no longer wipes
    violations pending on paths B, C, etc. Each path's violation lifecycle
    is independent.
    """
    vf = _violation_file_for_path(file_path)
    if vf.exists():
        try:
            vf.unlink()
        except OSError:
            pass


def _record_embodiment(signals, session_id):
    """Record embodiment observations per principle."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from lib.embodiment_tracker import EmbodimentTracker
        from collections import Counter

        tracker = EmbodimentTracker()
        principle_violations = Counter(s.principle for s in signals)
        for principle in ("DRY", "KISS", "BIG-O", "YAGNI"):
            v_count = principle_violations.get(principle, 0)
            state = tracker._states.get(principle)
            depth = 1 if v_count > 0 else (
                state.current_depth if state and state.current_depth >= 3 else 3
            )
            tracker.record_session(
                principle=principle,
                session_id=session_id,
                violation_count=v_count,
                convergence_depth=depth,
            )
        tracker.save()
    except Exception:
        pass


def _write_violation_state(file_path, signals, syntax_error, now, tool_name, agent_id=""):
    """Two-Hook Gate: write per-path violation state for PreToolUse enforcement (#2837).

    Stamps the writing actor's ``agent_id`` (#342): subagents share the
    parent Claude Code PID, so an unstamped marker written by a fleet
    agent would stop-the-line the PARENT session's unrelated edits.
    Empty string = the main session (payloads without an agent_id).
    """
    ENFORCEMENT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _violation_file_for_path(file_path).write_text(json.dumps({
            "file_path": file_path,
            "violations": [{"principle": s.principle, "severity": s.severity, "message": s.message} for s in signals],
            "syntax_error": syntax_error,
            "timestamp": now,
            "tool_name": tool_name,
            "agent_id": agent_id,
        }))
    except OSError:
        pass


def _validate_and_load(hook_input):
    """Validate hook input and load content. Returns (tool_name, file_path, content, now) or None."""
    if not hook_input:
        return None
    tool_name, tool_input, _ = extract_tool_info(hook_input)
    if tool_name not in ("Write", "Edit"):
        return None
    file_path = get_file_path(tool_name, tool_input)
    if not file_path or not file_path.endswith(".py"):
        return None
    now = time.time()
    debounce = load_debounce()
    if now - debounce.get(file_path, 0) < DEBOUNCE_SECONDS:
        return None
    content = _get_content(tool_name, tool_input, file_path)
    if not content:
        return None
    return tool_name, file_path, content, now


def _run_checks(content, file_path):
    """Run syntax + quality checks. Returns (syntax_error, signals)."""
    syntax_error = _check_syntax(content, file_path)
    try:
        from quality_fast import check_all
        return syntax_error, check_all(content, file_path)
    except ImportError:
        return syntax_error, []


def main():
    """Coordinator: parse, check, enforce, respond."""
    hook_input = parse_hook_input()
    ctx = _validate_and_load(hook_input)
    if not ctx:
        respond()
        return
    tool_name, file_path, content, now = ctx
    syntax_error, signals = _run_checks(content, file_path)
    debounce = load_debounce()
    debounce[file_path] = now
    save_debounce(debounce)
    if not signals and not syntax_error:
        _clear_violation_state_for_path(file_path)
        respond()
        return
    messages = []
    if syntax_error:
        messages.append(syntax_error)
    messages.extend(f"[QUALITY: {s.principle}] {s.message}" for s in signals)
    _record_embodiment(signals, hook_input.get("session_id", f"hook-{int(now)}"))
    # Only ERROR-severity signals trigger blocking enforcement (Gate V).
    # WARN signals (DIP, Feature Envy) remain advisory — shown in messages but
    # never written to enforcement state. Prevents deadlocks from low-precision checks.
    error_signals = [s for s in signals if s.severity == ERROR]
    if not error_signals and not syntax_error:
        _clear_violation_state_for_path(file_path)
        respond("\n".join(messages))
        return
    _write_violation_state(
        file_path, error_signals, syntax_error, now, tool_name,
        agent_id=hook_input.get("agent_id", ""),
    )
    respond("\n".join(messages))


if __name__ == "__main__":
    main()
