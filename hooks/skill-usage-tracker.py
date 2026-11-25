#!/usr/bin/env python3
"""
Skill Usage Tracker Hook
========================

PreToolUse hook that tracks Skill tool invocations for analytics.
Logs which skills are being used to identify usage patterns.

This hook:
- Only activates when tool_name is "Skill"
- Extracts skill name from tool_input
- Records to skill-usage.jsonl via lib/skill_analytics.log_invocation
- NEVER blocks - this is tracking, not enforcement
- Runs async: permission response format is not required

These records are the ONLY ground-truth signal of which skills are actually
used. The harness produces them; the model being measured does not report
them. That is what makes them admissible as genome fitness evidence.

History (why this calls in-process instead of shelling out): until 2026-08-01
this hook ran ``subprocess.run(["python3", ".../lib/skill-analytics.py", ...])``
without inspecting ``returncode``, then logged INFO success unconditionally.
When #836 (dcc92e55, 2026-03-06) deleted the hyphenated duplicate that the
PEP 8 rename in #730 had superseded, the call began exiting 2 and recording
nothing -- while still logging "Tracked skill usage". The last real record is
2026-03-06T16:42:45Z, ten minutes before that commit; 249 false-success lines
followed over the next 148 days. An in-process call cannot fail silently: a
missing or broken recorder raises, and the except branch logs ERROR.

Version: 1.2.0 (Gen 467; recorder re-attached 2026-08-01)
"""

import contextlib
import io
import sys
from pathlib import Path
from datetime import datetime, timezone

_HOOKS_DIR = Path(__file__).resolve().parent
_LIB_DIR = _HOOKS_DIR.parent / "lib"
for _p in (str(_HOOKS_DIR), str(_LIB_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hook_utils import parse_hook_input, respond  # noqa: E402

# Configuration
CLAUDE_DIR = Path.home() / ".claude"
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"


def log_hook(level: str, message: str):
    """Log to hook log file."""
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(HOOK_LOG, "a") as f:
            f.write(f"{timestamp} [SKILL-TRACK][{level}] {message}\n")
    except Exception:
        pass


def track_skill_usage(skill_name: str) -> bool:
    """Record one skill invocation. Returns True only if it was written.

    Calls ``skill_analytics.log_invocation`` in-process so that a missing or
    broken recorder raises here instead of being swallowed as a non-zero exit
    code from a subprocess. INFO is logged only on the success path; every
    failure logs ERROR with the exception type, so the recorder can never
    again go dead while the log claims otherwise.

    ``log_invocation`` prints a confirmation to stdout, which on a PreToolUse
    hook is the response channel -- it is redirected so it cannot corrupt the
    JSON the harness parses.
    """
    try:
        from skill_analytics import log_invocation

        with contextlib.redirect_stdout(io.StringIO()):
            log_invocation(skill_name, "hook")
    except Exception as exc:
        log_hook(
            "ERROR",
            f"Failed to track skill {skill_name!r}: "
            f"{type(exc).__name__}: {exc}",
        )
        return False

    log_hook("INFO", f"Tracked skill usage: {skill_name}")
    return True


def main():
    hook_input = parse_hook_input()
    if not hook_input:
        respond()
        return

    tool_name = hook_input.get("tool_name", "")

    # Only track Skill tool invocations
    if tool_name != "Skill":
        respond()
        return

    # Extract skill name from tool_input
    tool_input = hook_input.get("tool_input", {})
    skill_name = tool_input.get("skill", "")

    if skill_name:
        track_skill_usage(skill_name)

    # Always allow - this is just tracking (async hook, response ignored)
    respond()


if __name__ == "__main__":
    main()
