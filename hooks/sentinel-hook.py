#!/usr/bin/env python3
"""Sentinel hook — proof-of-execution recorder for all GRIP hooks.

INS-05: Every hook event writes a JSON record to the sentinel log.
A health check script (lib/hook-health.py) verifies completeness.

This hook MUST be the LAST hook in each event's list so it records
whether the pipeline reached the end (earlier hooks may deny/block).

Response: always {} (pass-through, never blocks).
"""

import json
import os
import sys
import time

CLAUDE_DIR = os.path.join(os.environ.get("HOME", "/tmp"), ".claude")
SENTINEL_LOG = os.path.join(CLAUDE_DIR, "cache", "sentinel.jsonl")
SESSION_ID = os.environ.get("CLAUDE_SESSION_ID", "unknown")
VENV_PYTHON = os.path.join(CLAUDE_DIR, "venv", "bin", "python3")
VENV_HEALTH_CACHE = os.path.join(CLAUDE_DIR, "cache", ".venv-health-ts")
VENV_CHECK_INTERVAL = 60  # seconds between full checks

# The single place the Notification payload contract is written down. When the
# log starts carrying `field_missing`, read `payload_keys` off that record and
# update this constant — the replacement name is in the log by construction.
NOTIFICATION_TYPE_FIELD = "notification_type"


def _parse_stdin():
    """Parse hook JSON from stdin. Returns empty dict on failure."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _check_venv_health():
    """Rate-limited venv health check. Returns status dict or None if skipped."""
    now = time.time()
    try:
        if os.path.exists(VENV_HEALTH_CACHE):
            last_check = os.path.getmtime(VENV_HEALTH_CACHE)
            if now - last_check < VENV_CHECK_INTERVAL:
                return None  # Too recent, skip
    except OSError:
        pass

    status = {"venv_exists": os.path.isfile(VENV_PYTHON)}
    if status["venv_exists"]:
        status["venv_executable"] = os.access(VENV_PYTHON, os.X_OK)
    else:
        status["venv_executable"] = False

    # Touch the cache to rate-limit
    try:
        os.makedirs(os.path.dirname(VENV_HEALTH_CACHE), exist_ok=True)
        with open(VENV_HEALTH_CACHE, "w") as f:
            f.write(str(int(now)))
    except OSError:
        pass

    return status


def _notification_context(hook_input):
    """Read the Notification type, and say so loudly when the field is gone.

    Claude Code sends this as `notification_type`, never `type` — the installed
    2.1.226 binary dispatches `case"Notification": a=n.notification_type`.
    Values include agent_needs_input and agent_completed.

    A payload carrying no such field at all is a CHANGED UPSTREAM CONTRACT, not
    a notification that happens to have no type. Recording "" for that is
    precisely what hid the previous break for 2,241 consecutive events: an empty
    value is indistinguishable from a real one that is blank, so nothing ever
    looked wrong. Absence is therefore recorded as an explicit anomaly instead,
    carrying the payload's real key names so the replacement field is readable
    straight out of the log, and lib/hook-health.py counts these and reports
    DEGRADED rather than leaving them to be noticed by someone reading the log.

    Only key NAMES are recorded, never values, so no payload content is logged.
    """
    if NOTIFICATION_TYPE_FIELD in hook_input:
        return {"type": hook_input[NOTIFICATION_TYPE_FIELD]}
    return {
        "type": None,
        "field_missing": NOTIFICATION_TYPE_FIELD,
        "payload_keys": sorted(hook_input),
    }


def _extract_context(event_type, hook_input):
    """Build context dict from event type and hook input."""
    if event_type in ("PreToolUse", "PostToolUse"):
        return {"tool": hook_input.get("tool_name", "")}
    if event_type in ("SubagentStart", "SubagentStop"):
        return {"agent_type": hook_input.get("agent_type", "")}
    if event_type == "UserPromptSubmit":
        return {"has_input": bool(hook_input.get("user_prompt", ""))}
    if event_type == "Notification":
        return _notification_context(hook_input)
    return {}


def _build_record(event_type, context, elapsed_ms, hook_input):
    """Assemble the sentinel log record with optional subagent fields."""
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event_type,
        "session": SESSION_ID[:12],
        "context": context,
        "sentinel_ms": elapsed_ms,
    }
    agent_id = hook_input.get("agent_id")
    agent_type = hook_input.get("agent_type")
    if agent_id:
        record["agent_id"] = agent_id[:12]
    if agent_type:
        record["agent_type"] = agent_type
    return record


def main():
    start = time.monotonic()
    hook_input = _parse_stdin()
    event_type = hook_input.get(
        "hook_event_name", os.environ.get("CLAUDE_HOOK_EVENT", "unknown")
    )
    context = _extract_context(event_type, hook_input)
    elapsed_ms = round((time.monotonic() - start) * 1000, 1)
    record = _build_record(event_type, context, elapsed_ms, hook_input)

    venv_status = _check_venv_health()
    if venv_status is not None:
        record["venv"] = venv_status

    try:
        os.makedirs(os.path.dirname(SENTINEL_LOG), exist_ok=True)
        with open(SENTINEL_LOG, "a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        pass  # Sentinel must never fail the pipeline

    print("{}")


if __name__ == "__main__":
    main()
