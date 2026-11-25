#!/usr/bin/env python3
"""Prompt Routing Hook — UserPromptSubmit fast path.

Fires on every UserPromptSubmit event. Exempt prompts short-circuit
in-process (<5ms). Non-exempt prompts spawn a detached worker that
writes the assessment to state/routing/last-assessment.json. The hook
itself exits immediately so it never delays the user experience.

The companion skill (skills/prompt-routing/SKILL.md) reads the
assessment file at the start of the agent's next reasoning step.

INTEGRATION:
    Add to ~/.claude/settings.json UserPromptSubmit array:
    {
      "hooks": [{
        "type": "command",
        "command": "python3 $HOME/.claude/hooks/prompt-routing-hook.py",
        "timeout": 3000,
        "statusMessage": "Routing"
      }]
    }

PERFORMANCE:
    - Target: < 50ms hook exit time
    - Worker subprocess detached (start_new_session=True)
    - Exempt prompts never spawn a worker
"""

import json
import subprocess
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
LIB_DIR = CLAUDE_DIR / "lib"
WORKER = CLAUDE_DIR / "hooks" / "prompt-routing-worker.py"
STATE_DIR = CLAUDE_DIR / "state" / "routing"
ASSESSMENT_FILE = STATE_DIR / "last-assessment.json"


def _extract_message(hook_input: dict) -> str:
    """Extract user message from hook input, trying multiple field names."""
    for key in ("message", "content", "user_message", "prompt"):
        if hook_input.get(key):
            return hook_input[key]
    return ""


def _check_exempt(message: str) -> bool:
    """In-process fast-path exemption check. Returns True if routing skipped.

    Imports lib/prompt_routing.is_exempt with graceful fallback if the
    library is unavailable (treat as exempt — fail-safe).
    """
    try:
        if str(LIB_DIR) not in sys.path:
            sys.path.insert(0, str(LIB_DIR))
        from prompt_routing import is_exempt  # type: ignore
        exempt, _ = is_exempt(message)
        return exempt
    except Exception:
        return True  # Fail-safe: any import/error → treat as exempt


def _spawn_worker(message: str) -> None:
    """Spawn the worker subprocess in a detached session and return immediately."""
    if not WORKER.exists():
        return
    try:
        subprocess.Popen(
            [sys.executable, str(WORKER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ).stdin.write(json.dumps({"message": message}).encode() + b"\n")
    except Exception:
        pass  # Fail-safe: worker spawn failure should not block the prompt


def _clear_stale_assessment() -> None:
    """Remove any pre-existing assessment file so stale data doesn't fire."""
    try:
        if ASSESSMENT_FILE.exists():
            ASSESSMENT_FILE.unlink()
    except OSError:
        pass


def main() -> None:
    """Hook entry point. Fast exit path, detached worker for real work."""
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    message = _extract_message(hook_input)
    if not message:
        sys.exit(0)

    _clear_stale_assessment()

    if _check_exempt(message):
        sys.exit(0)

    _spawn_worker(message)
    sys.exit(0)


if __name__ == "__main__":
    main()
