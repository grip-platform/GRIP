#!/usr/bin/env python3
"""
Learning Worker - Subprocess-isolated learning detection.

Runs in a child process so that lembed SIGSEGV crashes are contained.
The parent learning-hook.py survives and exits cleanly.

Input: JSON on stdin with {message, project_path}
Output: Notification text on stdout (if learning pattern detected)
"""

import json
import sys
from pathlib import Path

# Ensure venv packages are available
VENV_SITE = Path.home() / ".claude" / "venv" / "lib"
if VENV_SITE.exists():
    for sp in sorted(VENV_SITE.glob("python*/site-packages")):
        if str(sp) not in sys.path:
            sys.path.insert(0, str(sp))

CLAUDE_DIR = Path.home() / ".claude"
sys.path.insert(0, str(CLAUDE_DIR / "lib"))

LEARNING_DETECTOR = CLAUDE_DIR / "lib" / "learning_detector.py"


def main():
    try:
        worker_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    message = worker_input.get("message", "")
    project_path = worker_input.get("project_path", "")

    if not message or not LEARNING_DETECTOR.exists():
        sys.exit(0)

    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("learning_detector", str(LEARNING_DETECTOR))
    learning_detector = loader.load_module()

    result = learning_detector.process_message(
        message=message,
        novelty_score=0.0,
        project_path=project_path,
        auto_notify=True,
    )
    if result.get("notification"):
        print(result["notification"])


if __name__ == "__main__":
    main()
