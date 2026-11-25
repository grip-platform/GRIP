#!/usr/bin/env python3
"""StopFailure handler — RSI auto-recovery on rate limits and errors.

Harnesses the CC Stop hook to detect failure conditions and enable
automatic recovery for RSI sprints.

When the Stop event fires with an error payload:
1. Classifies the failure: rate_limit, auth_failure, network_error, context_limit
2. For rate_limit: writes backoff schedule for RSI orchestrator
3. For context_limit: triggers /save state persistence
4. For auth_failure: logs diagnostic (no retry — auth failures are permanent)
5. For all: writes state/stop-failure.json for session-start resurrection

Input (stdin JSON): Stop event with potential error info
Output: Status message (informational)

Origin: GH issue #1355 (CC 2.1.78 StopFailure harness).
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
STATE_DIR = CLAUDE_DIR / "state"
BACKOFF_FILE = STATE_DIR / "rate-limit-backoff.json"
FAILURE_FILE = STATE_DIR / "stop-failure.json"
FAILURE_LOG = CLAUDE_DIR / "learning" / "stop-failures.jsonl"


# Canonical CC StopFailure matcher error-type values (CC 2.1.78+) -> GRIP internal type.
# Source: code.claude.com/docs/en/hooks (StopFailure matcher table).
_CC_ERROR_TYPE_MAP = {
    "rate_limit": "rate_limit",
    "overloaded": "rate_limit",
    "authentication_failed": "auth_failure",
    "oauth_org_not_allowed": "auth_failure",
    "billing_error": "auth_failure",
    "server_error": "network_error",
    "max_output_tokens": "context_limit",
    "invalid_request": "unknown",
    "model_not_found": "unknown",
    "unknown": "unknown",
}


# Free-text markers per failure type, scanned over the whole payload as a fallback
# when no explicit CC error-type field is present. Table-driven to keep CC low.
_BLOB_MARKERS = (
    ("rate_limit", ("rate limit", "rate_limit", "429", "too many requests", "overloaded")),
    ("auth_failure", ("authentication", "auth_failure", "401", "403", "forbidden", "invalid_api_key", "oauth", "billing")),
    ("network_error", ("network", "connection", "timeout", "dns", "socket", "server_error")),
    ("context_limit", ("context", "token limit", "max_tokens", "context window", "max_output_tokens")),
)


def classify_failure(hook_input: dict) -> str:
    """Classify the Stop/StopFailure failure type.

    Returns: rate_limit, auth_failure, network_error, context_limit, clean_exit, unknown

    Schema-agnostic by design. CC's StopFailure (2.1.78) surfaces the error via a
    matcher error-type, but the exact input field name is not contract-stable, so we
    (1) map any explicit CC error-type field, then (2) fall back to scanning every
    string value in the payload. ``clean_exit`` is only reachable on the ``Stop``
    event -- a ``StopFailure`` invocation is always a real failure, never a no-op.
    """
    event = str(hook_input.get("hook_event_name", ""))

    # 1. Explicit CC error-type field (canonical StopFailure matcher values).
    for field in ("error_type", "failure_type", "reason", "stop_reason", "error", "matcher"):
        val = str(hook_input.get(field, "")).strip().lower()
        if val in _CC_ERROR_TYPE_MAP:
            return _CC_ERROR_TYPE_MAP[val]

    # 2. Schema-agnostic scan over every string value in the payload.
    blob = " ".join(str(v) for v in hook_input.values() if isinstance(v, str)).lower()
    for failure_type, markers in _BLOB_MARKERS:
        if any(marker in blob for marker in markers):
            return failure_type

    # 3. clean_exit only on the Stop event; StopFailure is always a real failure.
    if event != "StopFailure" and not hook_input.get("error") and not hook_input.get("stop_reason"):
        return "clean_exit"

    return "unknown"


def handle_rate_limit(hook_input: dict) -> str:
    """Write backoff schedule for RSI orchestrator."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Read existing backoff to increment retry count
    retry_count = 0
    if BACKOFF_FILE.exists():
        try:
            existing = json.loads(BACKOFF_FILE.read_text())
            retry_count = existing.get("retry_count", 0)
        except (json.JSONDecodeError, OSError):
            pass

    retry_count += 1
    # Exponential backoff: 30s, 60s, 120s, 240s, capped at 300s
    backoff_seconds = min(30 * (2 ** (retry_count - 1)), 300)

    backoff = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "failure_type": "rate_limit",
        "retry_count": retry_count,
        "backoff_seconds": backoff_seconds,
        "resume_after": datetime.fromtimestamp(
            time.time() + backoff_seconds, tz=timezone.utc
        ).isoformat(),
        "max_retries": 5,
        "exhausted": retry_count >= 5,
    }

    BACKOFF_FILE.write_text(json.dumps(backoff, indent=2))
    return f"rate_limit: backoff {backoff_seconds}s (retry #{retry_count})"


def handle_context_limit(hook_input: dict) -> str:
    """Trigger state persistence on context exhaustion."""
    try:
        # Try to capture DELIGHT snapshot
        sys.path.insert(0, str(CLAUDE_DIR))
        from lib.delight_v2 import capture_snapshot
        snap = capture_snapshot(elapsed_hours=2.0)
        return f"context_limit: state persisted, DELIGHT={snap.composite:.3f}"
    except Exception:
        return "context_limit: state persistence attempted"


def handle_auth_failure(hook_input: dict) -> str:
    """Log auth failure — no retry (permanent failure)."""
    return "auth_failure: permanent, no retry"


def write_failure_state(failure_type: str, hook_input: dict) -> None:
    """Write failure state for session-start resurrection."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    failure = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "failure_type": failure_type,
        "stop_reason": hook_input.get("stop_reason", ""),
        "recoverable": failure_type in ("rate_limit", "network_error", "context_limit"),
    }
    FAILURE_FILE.write_text(json.dumps(failure, indent=2))


def log_failure(failure_type: str) -> None:
    """Append failure to learning log for /evo analysis."""
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": failure_type,
    }
    try:
        with open(FAILURE_LOG, "a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        pass


def main():
    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        hook_input = {}

    failure_type = classify_failure(hook_input)

    if failure_type == "clean_exit":
        print("[GRIP Stop] clean exit")
        return

    # Handle by type
    handlers = {
        "rate_limit": handle_rate_limit,
        "auth_failure": handle_auth_failure,
        "context_limit": handle_context_limit,
    }

    handler = handlers.get(failure_type)
    if handler:
        result = handler(hook_input)
    else:
        result = f"{failure_type}: logged"

    write_failure_state(failure_type, hook_input)
    log_failure(failure_type)

    print(f"[GRIP StopFailure] {result}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
