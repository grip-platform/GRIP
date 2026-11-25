#!/usr/bin/env python3
"""Subagent lifecycle hook — telemetry for SubagentStart/SubagentStop.

Records subagent spawn/completion events to data/subagent-lifecycle.jsonl.
Provides visibility into which agents are spawned, how often, and duration.

Registered in settings.json for SubagentStart and SubagentStop events.
"""

import json
import os
import sys
import time

LIFECYCLE_LOG = os.path.join(
    os.environ.get("HOME", "/tmp"), ".claude", "data", "subagent-lifecycle.jsonl"
)


def _parse_stdin():
    """Parse hook JSON from stdin."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def main():
    hook_input = _parse_stdin()
    event = hook_input.get("hook_event_name", "unknown")
    agent_type = hook_input.get("agent_type", "unknown")
    agent_id = hook_input.get("agent_id", "")

    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
        "agent_type": agent_type,
        "agent_id": agent_id[:12] if agent_id else "",
        "session": os.environ.get("CLAUDE_SESSION_ID", "unknown")[:12],
    }

    try:
        os.makedirs(os.path.dirname(LIFECYCLE_LOG), exist_ok=True)
        with open(LIFECYCLE_LOG, "a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        pass

    print("{}")


if __name__ == "__main__":
    main()
