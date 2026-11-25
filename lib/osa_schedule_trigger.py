"""
osa_schedule_trigger — invoke OSA macros directly from launchd (no WhatsApp JID needed).

Usage:
    python3 lib/osa_schedule_trigger.py morning
    python3 lib/osa_schedule_trigger.py wrap

Called by ~/Library/LaunchAgents/com.grip.morning-macro.plist and
com.grip.wrap-macro.plist at their scheduled times. Output goes to
~/.claude/logs/launchd-<macro>.log via the plist's StandardOutPath.
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
LOG_DIR = CLAUDE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# launchd doesn't inherit PATH — bootstrap PYTHONPATH so osa_phone_bridge imports work.
if str(CLAUDE_DIR) not in sys.path:
    sys.path.insert(0, str(CLAUDE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def run(macro_name: str, bridge=None) -> int:
    from lib.osa_phone_bridge import OsaPhoneBridge, MACRO_NAMES

    if macro_name not in MACRO_NAMES:
        log.error("Unknown macro %r — valid: %s", macro_name, ", ".join(sorted(MACRO_NAMES)))
        return 1

    if bridge is None:
        bridge = OsaPhoneBridge()
    log.info("Scheduled trigger: /mac %s", macro_name)
    result = bridge._run_macro(macro_name, max_tier=2)
    log.info("Result:\n%s", result)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <macro_name>", file=sys.stderr)
        sys.exit(1)
    sys.exit(run(sys.argv[1]))
