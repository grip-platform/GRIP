#!/usr/bin/env python3
"""HAL memory preflight hook — fires before HAL Server and HAL Council invocations.

PreToolUse hook. Checks memory pressure in ~80ms (read-only probe, no LLM).
If YELLOW or RED, emits a compact warning to stderr that surfaces in the session
BEFORE the HAL call proceeds.

Wiring in settings.json (PreToolUse, matcher: Bash):
  The hook intercepts Bash tool calls that contain hal-council, hal-server, /hal,
  grip-speak, or agora-demo invocations and prepends a memory advisory if needed.

Banach property:
  - GREEN system: zero output (noop — fixed point)
  - YELLOW/RED: one advisory line, call allowed to proceed

The hook NEVER blocks. It is always advisory. The operator decides whether to
run /mem act before the HAL invocation.

Security:
  - Read-only probe only — never executes memory actions
  - No network calls, no LLM calls, no side effects
"""

import json
import os
import sys

CLAUDE_DIR = os.path.join(os.environ.get("HOME", "/tmp"), ".claude")
HAL_KEYWORDS = frozenset([
    "hal-council", "hal-server", "hal_council", "hal_server",
    "/hal", "grip-speak", "agora-demo", "agora_demo",
    "lib/hal/", "lib.hal.", "bin/hal",
])


def _parse_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _is_hal_invocation(event: dict) -> bool:
    """True if this Bash call is likely a HAL server or council invocation."""
    tool = event.get("tool_name", "")
    if tool not in ("Bash", "mcp__grip-pair__grip_load_skill"):
        return False
    inp = event.get("tool_input", {})
    cmd = inp.get("command", "") if isinstance(inp, dict) else str(inp)
    cmd_lower = cmd.lower()
    return any(kw in cmd_lower for kw in HAL_KEYWORDS)


def _probe_memory():
    """Probe memory state. Returns MemoryState or None on import failure."""
    sys.path.insert(0, CLAUDE_DIR)
    try:
        from lib.hal_mem import probe
        return probe()
    except Exception:
        return None


def main():
    event = _parse_stdin()

    if not _is_hal_invocation(event):
        print(json.dumps({}))
        return

    state = _probe_memory()
    if state is None:
        # Can't probe — allow call to proceed silently
        print(json.dumps({}))
        return

    from lib.hal_mem import is_fixed_point, fmt_summary
    if is_fixed_point(state):
        # Green — noop. Fixed-point: no output.
        print(json.dumps({}))
        return

    # YELLOW or RED — emit advisory to stderr (surfaces in CC session)
    summary = fmt_summary(state)
    advisory = (
        f"\n[MEM PREFLIGHT] {summary}\n"
        f"    → Run /mem act to recover memory before the HAL call. Or proceed anyway.\n"
    )
    sys.stderr.write(advisory)
    sys.stderr.flush()

    # Always allow — never block HAL calls
    print(json.dumps({}))


if __name__ == "__main__":
    main()
