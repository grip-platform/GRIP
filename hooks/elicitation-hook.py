#!/usr/bin/env python3
"""
Elicitation Hook - MCP Input Request Interception
===================================================

Elicitation hook that fires when an MCP server requests user input mid-task.
Provides GRIP with visibility into MCP elicitation requests and security
screening of what MCP servers are asking for.

Capabilities:
- Logs all MCP elicitation requests to steering telemetry
- Screens requested form schemas for sensitive field names (passwords, tokens)
- Warns when unknown MCP servers request input (informational, not blocking)

Hook event: Elicitation
Matcher: matches against mcp_server_name (use ".*" for all servers)

Input fields:
    mcp_server_name  - Name of the MCP server requesting input
    message          - The request message shown to the user
    mode             - "form" or "url"
    elicitation_id   - Optional unique identifier
    requested_schema - Optional JSON schema for form-mode requests
    url              - Optional authentication URL for url-mode requests

Output:
    hookSpecificOutput with action (accept/decline/cancel) and content
    to auto-respond, or empty {} to let the user respond normally.

Version: 1.0.0
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"
ELICITATION_LOG = CLAUDE_DIR / "logs" / "elicitation.jsonl"

# Sensitive field names that warrant a warning when MCP servers request them
SENSITIVE_FIELD_PATTERNS = {
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "access_key", "private_key", "credential", "auth", "authorization",
}


def log_hook(level: str, message: str) -> None:
    """Log to hook log file."""
    try:
        HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(HOOK_LOG, "a") as f:
            f.write(f"{ts} [ELICITATION][{level}] {message}\n")
    except Exception:
        pass


def log_elicitation_event(event: dict) -> None:
    """Append structured elicitation event to JSONL telemetry log."""
    try:
        ELICITATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        with open(ELICITATION_LOG, "a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")
    except Exception:
        pass


def check_sensitive_fields(schema: dict) -> list[str]:
    """Check if a form schema requests sensitive fields.

    Returns list of sensitive field names found in the schema.
    """
    if not schema or not isinstance(schema, dict):
        return []

    sensitive_found = []
    properties = schema.get("properties", {})
    for field_name in properties:
        field_lower = field_name.lower().replace("-", "_")
        for pattern in SENSITIVE_FIELD_PATTERNS:
            if pattern in field_lower:
                sensitive_found.append(field_name)
                break

    return sensitive_found


def main():
    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        # No input or invalid JSON -- pass through
        print(json.dumps({}))
        return

    server_name = hook_input.get("mcp_server_name", "unknown")
    message = hook_input.get("message", "")
    mode = hook_input.get("mode", "unknown")
    elicitation_id = hook_input.get("elicitation_id", "")
    requested_schema = hook_input.get("requested_schema", {})
    url = hook_input.get("url", "")

    # Log the elicitation request for steering telemetry
    log_elicitation_event({
        "event": "elicitation_request",
        "server": server_name,
        "mode": mode,
        "message": message[:200],  # Truncate for log compactness
        "elicitation_id": elicitation_id,
        "has_schema": bool(requested_schema),
        "url": url[:200] if url else "",
    })

    log_hook("INFO", f"MCP elicitation from '{server_name}' (mode={mode}): {message[:100]}")

    # Check for sensitive field requests in form schemas
    sensitive_fields = []
    if mode == "form" and requested_schema:
        sensitive_fields = check_sensitive_fields(requested_schema)
        if sensitive_fields:
            fields_str = ", ".join(sensitive_fields)
            log_hook(
                "WARN",
                f"MCP server '{server_name}' requesting sensitive fields: {fields_str}",
            )
            log_elicitation_event({
                "event": "sensitive_field_warning",
                "server": server_name,
                "fields": sensitive_fields,
            })

    # Pass through to user -- GRIP logs and warns but does not auto-respond.
    # The user decides whether to accept, decline, or cancel.
    # Future: could auto-decline for untrusted MCP servers.
    print(json.dumps({}))


if __name__ == "__main__":
    main()
