#!/usr/bin/env python3
"""W9 PreToolUse hook — deny ship operations on unpropagated operational insights.

Per plans/auto-insight-rsi-sprint-2026-05-05.md W9. Fires before
``Bash`` tool calls matching ``^(gh pr (merge|create)|git push)``.
Reads the current session's unacted ledger entries and:

- DENY tier (``operational_discovery``):  ANY unacted -> block ship
- WARN tier (``design_pattern`` / ``behavioral_rule``): >5 unacted -> block;
  <=5 -> implicit allow (operator can ship; insights still pending)
- INFO tier (``code_change`` / ``documentation_gap`` / unclassified): NEVER blocks

Operator escape hatch: ``GRIP_INSIGHT_PROPAGATION_BYPASS=1`` allows the ship
unconditionally. Every bypass appends one line to
``logs/insight-propagation-bypass.log`` (timestamp, command, queue depth,
session_id). Transparent but not silent — kill-switch K2 watches the rate.

Fails open: any exception during the check writes to
``logs/insight-deny-errors.jsonl`` and the hook returns allow. The principle
is "don't block ship on hook bugs"; the audit trail catches false negatives.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from pathlib import Path


_LIB_OVERRIDE = os.environ.get("GRIP_LIB_PATH")
if _LIB_OVERRIDE:
    _LIB = Path(_LIB_OVERRIDE)
else:
    _LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_LIB))


SHIP_RE = re.compile(r"^(gh\s+pr\s+(merge|create)|git\s+push)\b")
WARN_THRESHOLD = 5
DENY_CLASSIFICATIONS = {"operational_discovery"}
WARN_CLASSIFICATIONS = {"design_pattern", "behavioral_rule"}


def _allow() -> int:
    """PreToolUse allow — emit empty JSON, exit 0."""
    sys.stdout.write("{}\n")
    return 0


def _deny(reason: str) -> int:
    """PreToolUse deny — emit the modern permissionDecision JSON, exit 0.

    The old top-level {"decision": "deny"} was invalid vocabulary and made
    this deny silently non-enforcing (see lib/hook_response.py).
    """
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}) + "\n")
    return 0


def _log_bypass(command: str, queue_depth: int, session_id: str, reason: str) -> None:
    log = Path.home() / ".claude" / "logs" / "insight-propagation-bypass.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "command": command[:120],
            "queue_depth": queue_depth,
            "session_id": session_id,
            "reason": reason,
        }
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass  # Never block on logging.


def _log_error(error: Exception) -> None:
    log = Path.home() / ".claude" / "logs" / "insight-deny-errors.jsonl"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "error": repr(error),
            "trace": traceback.format_exc(),
        }
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _classify_unacted(unacted: list) -> tuple[list, list]:
    """Split unacted attempts into (deny-tier, warn-tier) lists."""
    deny = [a for a in unacted if a.classification in DENY_CLASSIFICATIONS]
    warn = [a for a in unacted if a.classification in WARN_CLASSIFICATIONS]
    return deny, warn


def _explain_deny_operational(count: int) -> str:
    return (
        f"{count} operational insight(s) from this session have not been "
        f"propagated to canonical files. These contain channel IDs / endpoint "
        f"URLs / keychain entries / port references that future sessions on "
        f"other machines need. Run /save to drain the queue, OR set "
        f"GRIP_INSIGHT_PROPAGATION_BYPASS=1 to ship anyway (audit-logged)."
    )


def _explain_deny_design(count: int) -> str:
    return (
        f"{count} design / behavioural insights pending propagation; threshold "
        f"is {WARN_THRESHOLD}. Run /save to drain the queue, OR set "
        f"GRIP_INSIGHT_PROPAGATION_BYPASS=1 to ship anyway (audit-logged)."
    )


def main() -> int:
    """Entry point. Always returns 0 (allow vs deny carried in stdout JSON)."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return _allow()

    if not raw.strip():
        return _allow()

    try:
        hook_input = json.loads(raw)
    except json.JSONDecodeError:
        return _allow()

    if hook_input.get("tool_name") != "Bash":
        return _allow()

    cmd = (hook_input.get("tool_input") or {}).get("command", "").strip()
    if not SHIP_RE.match(cmd):
        return _allow()

    try:
        from propagation_ledger import current_session_id, unacted_in_session
        session_id = current_session_id()
        unacted = unacted_in_session(session_id)
    except Exception as e:
        _log_error(e)
        return _allow()

    if os.environ.get("GRIP_INSIGHT_PROPAGATION_BYPASS") == "1":
        _log_bypass(cmd, queue_depth=len(unacted), session_id=session_id, reason="env-var")
        return _allow()

    deny, warn = _classify_unacted(unacted)
    if deny:
        return _deny(_explain_deny_operational(len(deny)))
    if len(warn) > WARN_THRESHOLD:
        return _deny(_explain_deny_design(len(warn)))
    return _allow()


if __name__ == "__main__":
    sys.exit(main())
