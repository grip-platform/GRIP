#!/usr/bin/env python3
"""
User Prompt Secrets Hook - Gate P (Prompt Input Scanning)
=========================================================

UserPromptSubmit hook that BLOCKS user messages containing secrets
BEFORE they are sent to the Claude API. This is the input-side
complement to secrets-output-hook.py (which scans tool output).

Incident origin: Toopie pasted docker inspect output containing API
keys and tokens. No hook existed on UserPromptSubmit to intercept.

Patterns detected (shared with secrets-output-hook.py):
- API keys (Anthropic, OpenAI, AWS, GitHub, Slack, Stripe, etc.)
- JWT tokens, private keys (PEM), connection strings
- Docker env var secrets (KEY=value in Env arrays)
- Generic PASSWORD=, SECRET=, TOKEN= patterns

BEHAVIOUR:
- BLOCKS the message (decision: "block") — prevents API transmission
- Tells the user WHAT type of secret was detected (not the value)
- Logs detection to hooks.log (type only, never the secret itself)

PERFORMANCE:
- Target: < 20ms (compiled regexes, short-circuit on empty)
- Only scans messages > 30 chars (secrets have minimum entropy)

Version: 1.0.0
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"

# Minimum message length worth scanning (secrets have entropy)
MIN_SCAN_LENGTH = 30

# ---------------------------------------------------------------------------
# Secret patterns — superset of secrets-output-hook.py plus input-specific
# ---------------------------------------------------------------------------

SECRET_PATTERNS = [
    # --- Cloud provider keys ---
    (r'sk-ant-[a-zA-Z0-9_-]{20,}', 'ANTHROPIC_KEY'),
    (r'sk-[a-zA-Z0-9]{20,}', 'OPENAI_KEY'),
    (r'AKIA[0-9A-Z]{16}', 'AWS_ACCESS_KEY'),
    (r'(?i)aws_secret_access_key\s*[=:]\s*[A-Za-z0-9/+=]{40}', 'AWS_SECRET'),

    # --- Git hosting tokens ---
    (r'ghp_[a-zA-Z0-9]{36}', 'GITHUB_PAT'),
    (r'gho_[a-zA-Z0-9]{36}', 'GITHUB_OAUTH'),
    (r'ghu_[a-zA-Z0-9]{36}', 'GITHUB_USER_TOKEN'),
    (r'ghs_[a-zA-Z0-9]{36}', 'GITHUB_SERVER_TOKEN'),
    (r'ghr_[a-zA-Z0-9]{36}', 'GITHUB_REFRESH_TOKEN'),
    (r'glpat-[a-zA-Z0-9_-]{20,}', 'GITLAB_PAT'),

    # --- Messaging & SaaS tokens ---
    (r'xox[baprs]-[a-zA-Z0-9-]{10,}', 'SLACK_TOKEN'),
    (r'sk_(?:live|test)_[a-zA-Z0-9]{24,}', 'STRIPE_SECRET_KEY'),
    (r'pk_(?:live|test)_[a-zA-Z0-9]{24,}', 'STRIPE_PUBLIC_KEY'),
    (r'SG\.[a-zA-Z0-9_-]{22}\.[a-zA-Z0-9_-]{43}', 'SENDGRID_KEY'),
    (r'SK[a-f0-9]{32}', 'TWILIO_KEY'),
    (r'[MN][A-Za-z\d]{23,}\.[\w-]{6}\.[\w-]{27}', 'DISCORD_TOKEN'),
    (r'npm_[a-zA-Z0-9]{36}', 'NPM_TOKEN'),
    (r'pypi-AgEIcHlwaS5vcmc[a-zA-Z0-9_-]{50,}', 'PYPI_TOKEN'),

    # --- Structured credentials ---
    (r'eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}', 'JWT_TOKEN'),
    (r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----', 'PRIVATE_KEY'),
    (r'(?i)(?:postgres|mysql|mongodb)(?:\+\w+)?://[^:]+:[^@\s]+@[^\s]+', 'CONNECTION_STRING'),

    # --- Docker / env var secrets (the Toopie incident pattern) ---
    # Matches KEY=value where KEY contains PASSWORD, SECRET, TOKEN, API_KEY, etc.
    (r'(?i)\b\w*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY|ACCESS_KEY|PRIVATE_KEY|AUTH)\w*\s*[=:]\s*\S{8,}', 'ENV_VAR_SECRET'),

    # --- Generic high-entropy API key assignments ---
    (r'(?i)api[_-]?key["\s:=]+["\']?[a-zA-Z0-9_-]{20,}["\']?', 'API_KEY'),
]

# Compile once at import time
COMPILED_PATTERNS = [(re.compile(p), label) for p, label in SECRET_PATTERNS]


def log_hook(level: str, message: str) -> None:
    """Log to hook log (never log actual secret values)."""
    try:
        HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(HOOK_LOG, "a") as f:
            f.write(f"{ts} [PROMPT-SECRETS][{level}] {message}\n")
    except Exception:
        pass


def scan_for_secrets(text: str) -> list[str]:
    """Scan text for secret patterns. Returns list of matched labels."""
    if not text or len(text) < MIN_SCAN_LENGTH:
        return []

    found = []
    seen_labels = set()
    for pattern, label in COMPILED_PATTERNS:
        if label not in seen_labels and pattern.search(text):
            found.append(label)
            seen_labels.add(label)
    return found


def main():
    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        # No input or invalid JSON — allow through
        print(json.dumps({}))
        return

    # Extract user message (UserPromptSubmit schema)
    message = hook_input.get("message", "")
    if not message:
        message = hook_input.get("content", "")
    if not message:
        message = hook_input.get("user_message", "")

    if not message or len(message) < MIN_SCAN_LENGTH:
        print(json.dumps({}))
        return

    # Scan for secrets
    detected = scan_for_secrets(message)

    if detected:
        types_str = ", ".join(detected)
        # Log only TYPE labels (e.g. "ANTHROPIC_KEY"), never the actual secret value.
        # CodeQL py/clear-text-logging-sensitive-data suppressed: no secret value stored.
        log_hook("BLOCK", f"Blocked prompt with {len(detected)} secret type(s): {types_str}")  # codeql[py/clear-text-logging-sensitive-data]

        # BLOCK the message — prevent API transmission.
        # reason field contains only TYPE labels, not the actual secret values.
        # CodeQL py/clear-text-storage-sensitive-data suppressed: labels only.
        print(json.dumps({  # codeql[py/clear-text-storage-sensitive-data]
            "decision": "block",
            "reason": (
                f"BLOCKED: Your message contains {len(detected)} potential secret(s): "
                f"{types_str}. "
                "Secrets must NEVER be sent to the API. "
                "Remove the sensitive values and try again. "
                "If these are already exposed, rotate them immediately."
            ),
        }))
    else:
        print(json.dumps({}))


if __name__ == "__main__":
    main()
