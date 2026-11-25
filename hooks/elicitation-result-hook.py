#!/usr/bin/env python3
"""
Elicitation Result Hook - MCP Response Interception
=====================================================

ElicitationResult hook that fires after a user responds to an MCP
elicitation, before the response is sent back to the MCP server.
Provides secrets scanning and decision-point logging for GRIP steering.

Capabilities:
- Scans user-provided form data for secrets before MCP server receives it
- Blocks responses containing detected secrets (exit code 2 = decline)
- Logs all elicitation decisions to steering telemetry

Hook event: ElicitationResult
Matcher: matches against mcp_server_name (use ".*" for all servers)

Input fields:
    mcp_server_name  - Name of the MCP server
    user_action      - "accept", "decline", or "cancel"
    user_content     - Optional form data provided by the user
    message          - Original request message
    mode             - "form" or "url"
    elicitation_id   - Optional unique identifier
    requested_schema - Optional original JSON schema

Output:
    hookSpecificOutput to override action/content, or empty {} to pass through.
    Exit code 2 forces action to "decline" (blocks the response).

Version: 1.0.0
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"
ELICITATION_LOG = CLAUDE_DIR / "logs" / "elicitation.jsonl"

# Secret patterns -- reused from user-prompt-secrets-hook.py core set.
# Only the most critical patterns to keep this hook fast (<20ms target).
SECRET_PATTERNS = [
    (r"sk-ant-[a-zA-Z0-9_-]{20,}", "ANTHROPIC_KEY"),
    (r"sk-[a-zA-Z0-9]{20,}", "OPENAI_KEY"),
    (r"AKIA[0-9A-Z]{16}", "AWS_ACCESS_KEY"),
    (r"ghp_[a-zA-Z0-9]{36}", "GITHUB_PAT"),
    (r"gho_[a-zA-Z0-9]{36}", "GITHUB_OAUTH"),
    (r"xox[baprs]-[a-zA-Z0-9-]{10,}", "SLACK_TOKEN"),
    (r"sk_(?:live|test)_[a-zA-Z0-9]{24,}", "STRIPE_KEY"),
    (r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}", "JWT_TOKEN"),
    (r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", "PRIVATE_KEY"),
    (r"(?i)(?:postgres|mysql|mongodb)(?:\+\w+)?://[^:]+:[^@\s]+@[^\s]+", "CONNECTION_STRING"),
    (r"glpat-[a-zA-Z0-9_-]{20,}", "GITLAB_PAT"),
]

COMPILED_PATTERNS = [(re.compile(p), label) for p, label in SECRET_PATTERNS]


def log_hook(level: str, message: str) -> None:
    """Log to hook log file."""
    try:
        HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(HOOK_LOG, "a") as f:
            f.write(f"{ts} [ELICITATION-RESULT][{level}] {message}\n")
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


def scan_content_for_secrets(content: dict) -> list[str]:
    """Scan form content values for secret patterns.

    Returns list of detected secret type labels.
    """
    if not content or not isinstance(content, dict):
        return []

    detected = []
    seen = set()
    # Flatten all values to strings for scanning
    text = " ".join(str(v) for v in content.values() if v)

    for pattern, label in COMPILED_PATTERNS:
        if label not in seen and pattern.search(text):
            detected.append(label)
            seen.add(label)

    return detected


def _handle_accepted(server_name: str, user_content: dict, elicitation_id: str) -> None:
    """Scan accepted elicitation content for secrets; exit(2) to decline if found."""
    if not (user_content and isinstance(user_content, dict)):
        log_hook("INFO", f"User accepted elicitation from '{server_name}'")
        print(json.dumps({}))
        return

    detected = scan_content_for_secrets(user_content)
    if not detected:
        log_hook("INFO", f"User accepted elicitation from '{server_name}'")
        print(json.dumps({}))
        return

    types_str = ", ".join(detected)
    log_hook("BLOCK", f"Blocked elicitation response to '{server_name}': secrets detected ({types_str})")
    # Stores only type labels (e.g. "GITHUB_PAT"), never the actual values.
    # CodeQL py/clear-text-storage-sensitive-data suppressed: labels only.
    log_elicitation_event({  # codeql[py/clear-text-storage-sensitive-data]
        "event": "elicitation_blocked",
        "server": server_name,
        "secret_types": detected,
        "elicitation_id": elicitation_id,
    })
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "ElicitationResult", "action": "decline"}}))
    sys.exit(2)


def _parse_hook_input() -> dict | None:
    """Parse hook JSON from stdin. Returns None on invalid input."""
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def main():
    hook_input = _parse_hook_input()
    if hook_input is None:
        print(json.dumps({}))
        return

    server_name = hook_input.get("mcp_server_name", "unknown")
    user_action = hook_input.get("user_action", "unknown")
    user_content = hook_input.get("user_content", {})
    mode = hook_input.get("mode", "unknown")
    elicitation_id = hook_input.get("elicitation_id", "")

    # Log structural metadata only — no content values, no secret data.
    # CodeQL py/clear-text-storage-sensitive-data suppressed: only type labels stored.
    log_elicitation_event({  # codeql[py/clear-text-storage-sensitive-data]
        "event": "elicitation_response",
        "server": server_name,
        "action": user_action,
        "mode": mode,
        "elicitation_id": elicitation_id,
        "has_content": bool(user_content),
    })

    if user_action in ("decline", "cancel"):
        log_hook("INFO", f"User {user_action}d elicitation from '{server_name}'")
        print(json.dumps({}))
        return

    _handle_accepted(server_name, user_content, elicitation_id)


if __name__ == "__main__":
    main()
