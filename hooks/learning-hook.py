#!/usr/bin/env python3
"""
Learning Hook - UserPromptSubmit Integration

Lightweight hook that processes user messages through learning-detector.py.
Outputs [GRIP LEARNING] signal when learning patterns are detected.

INTEGRATION:
    Add to ~/.claude/settings.json:
    {
      "hooks": {
        "UserPromptSubmit": [{
          "hooks": [{
            "type": "command",
            "command": "python3 ~/.claude/hooks/learning-hook.py"
          }]
        }]
      }
    }

PERFORMANCE:
    - Target: < 50ms execution
    - Only processes messages > 50 chars
    - Skips common non-learning patterns

VERSION: 1.0.0
DATE: 2026-01-23
"""

import json
import os
import sys
from pathlib import Path

# Constants
MIN_MESSAGE_LENGTH = 50
CLAUDE_DIR = Path.home() / ".claude"
LEARNING_DETECTOR = CLAUDE_DIR / "lib" / "learning_detector.py"

# Quick-skip patterns (common non-learning messages)
SKIP_PATTERNS = [
    "hello",
    "hi ",
    "thanks",
    "thank you",
    "yes",
    "no",
    "ok",
    "okay",
    "sure",
    "please",
    "help",
    "/",  # Commands
]


def should_skip(message: str) -> bool:
    """Check if message should be skipped (too short or common pattern)."""
    message_lower = message.lower().strip()

    # Skip short messages
    if len(message_lower) < MIN_MESSAGE_LENGTH:
        return True

    # Skip common patterns
    for pattern in SKIP_PATTERNS:
        if message_lower.startswith(pattern):
            return True

    return False


def _extract_message(hook_input: dict) -> str:
    """Extract user message from hook input, trying multiple field names."""
    for key in ("message", "content", "user_message"):
        if hook_input.get(key):
            return hook_input[key]
    return ""


def _run_detection(message: str) -> None:
    """Run learning detection in a subprocess to isolate lembed SIGSEGV."""
    import subprocess
    worker = CLAUDE_DIR / "hooks" / "learning-worker.py"
    if not worker.exists():
        return

    worker_input = json.dumps({"message": message, "project_path": os.getcwd()})
    try:
        # run_text, not subprocess.run(text=True): the worker's stdout is
        # printed verbatim into Claude's context a few lines below.
        _lib = str(CLAUDE_DIR / "lib")
        if _lib not in sys.path:
            sys.path.insert(0, _lib)
        from _compat import run_text
        result = run_text(
            [sys.executable, str(worker)],
            input=worker_input,
            capture_output=True, timeout=8,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(result.stdout.strip())
        elif result.returncode < 0:
            log_file = CLAUDE_DIR / "logs" / "hooks.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, "a") as f:
                f.write(f"[LEARNING-HOOK] Worker killed by signal {-result.returncode} (SIGSEGV isolated)\n")
    except subprocess.TimeoutExpired:
        pass


def main():
    """Process user message from stdin hook input."""
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    message = _extract_message(hook_input)
    if not message or should_skip(message):
        sys.exit(0)

    # Skip in commander sessions — avoids lembed SIGSEGV from concurrent model loading
    if os.environ.get("GRIP_GUILD_BOT") == "1" or os.environ.get("CLAUDE_NON_INTERACTIVE"):
        sys.exit(0)

    if not LEARNING_DETECTOR.exists():
        sys.exit(0)

    try:
        _run_detection(message)
    except Exception as e:
        log_file = CLAUDE_DIR / "logs" / "hooks.log"
        with open(log_file, "a") as f:
            f.write(f"[LEARNING-HOOK] Error: {e}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
