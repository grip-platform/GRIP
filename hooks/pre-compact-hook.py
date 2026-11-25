#!/usr/bin/env python3
"""
GRIP PreCompact hook -- captures working state for compaction summary.

When Claude Code auto-compacts, this hook's stdout is injected as custom
instructions into the compaction summary. This preserves GRIP working state
(CUBE position, wave, genome, mode, focus, blockers) across compaction
boundaries so the AI retains context.

Input (stdin JSON): {hook_event_name: "PreCompact", trigger: "auto"|"manual"}
Output (stdout, raw text -- NOT JSON): Structured context block, max 800 chars.

Performance target: <500ms. File reads only, no vector search.
Safety: Every gather function wraps in try/except. Hook exits 0 always.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", os.path.expanduser("~/.claude")))
LIB_DIR = CLAUDE_DIR / "lib"

# Add lib/ to path for imports
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


def main() -> None:
    # 1. Force-persist session facts (PRs, branches, files, decisions)
    try:
        subprocess.run(
            [sys.executable, str(LIB_DIR / "compaction_persist.py"), "--force"],
            timeout=3, capture_output=True
        )
    except Exception:
        pass

    # 2. Gather all state
    try:
        from compaction_state_gatherer import gather_all, format_compact
        state = gather_all()
        output = format_compact(state, max_chars=800)
        print(output)
    except Exception as e:
        # Fallback: minimal output so compaction still gets something
        print(f"[GRIP STATE] Gather failed: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Hook must NEVER fail
        pass
    sys.exit(0)
