#!/usr/bin/env python3
"""
Secrets Output Hook - Gate E
============================

PostToolUse hook that scans tool output for secret shapes and, when it finds
one, WARNS the model not to repeat the value. The warning reaches the model as
additionalContext, and the operator sees one line as a systemMessage. It does
not redact and it cannot block: a PostToolUse hook cannot modify tool output,
and by the time it runs the output, secret included, is already in the model's
context. What it buys is the warning and the audit line; keeping a secret out
of the context in the first place is the job of the PreToolUse gates and of not
printing secrets. (Docstring corrected 2026-10-18; the code below said so since
the TODO #8 fix, and the docstring claimed redaction, lane 2 finding 3. Until
2026-11-24 the warning went out as a bare systemMessage, which reached the
operator and never the model: GRIP#9497.)

Patterns detected:
- Anthropic API keys (sk-ant-*)
- OpenAI API keys (sk-*)
- AWS access keys (AKIA*)
- GitHub tokens (ghp_*, gho_*, ghu_*, ghs_*, ghr_*)
- JWT tokens
- Private keys (PEM format)
- Connection strings with passwords

Version: 1.1.0
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_HOOK_TREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(_HOOK_TREE / "lib"))
from claude_dir import get_claude_dir  # noqa: E402
from hook_response import emit, posttool_context  # noqa: E402
from hook_utils import parse_hook_input, respond  # noqa: E402

# Hook log, under $CLAUDE_DIR when it is set (rules/path-portability.md)
CLAUDE_DIR = get_claude_dir()
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"

# Only tools whose output can carry a secret are scanned.
CHECKED_TOOLS = {"Bash", "Read", "Glob", "Grep", "WebFetch"}

# Secret patterns to detect and redact
SECRET_PATTERNS = [
    # Anthropic API keys
    (r'sk-ant-[a-zA-Z0-9_-]{20,}', '[REDACTED:ANTHROPIC_KEY]'),
    
    # OpenAI API keys
    (r'sk-[a-zA-Z0-9]{20,}', '[REDACTED:OPENAI_KEY]'),
    
    # AWS Access Keys
    (r'AKIA[0-9A-Z]{16}', '[REDACTED:AWS_ACCESS_KEY]'),
    (r'aws_secret_access_key\s*[=:]\s*[A-Za-z0-9/+=]{40}', '[REDACTED:AWS_SECRET]'),
    
    # GitHub tokens
    (r'ghp_[a-zA-Z0-9]{36}', '[REDACTED:GITHUB_PAT]'),
    (r'gho_[a-zA-Z0-9]{36}', '[REDACTED:GITHUB_OAUTH]'),
    (r'ghu_[a-zA-Z0-9]{36}', '[REDACTED:GITHUB_USER]'),
    (r'ghs_[a-zA-Z0-9]{36}', '[REDACTED:GITHUB_SERVER]'),
    (r'ghr_[a-zA-Z0-9]{36}', '[REDACTED:GITHUB_REFRESH]'),
    
    # Generic API keys (common patterns)
    (r'(?i)api[_-]?key["\s:=]+["\']?[a-zA-Z0-9_-]{20,}["\']?', '[REDACTED:API_KEY]'),
    
    # JWT tokens (basic pattern)
    (r'eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}', '[REDACTED:JWT_TOKEN]'),
    
    # Private keys (PEM format headers)
    (r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----', '[REDACTED:PRIVATE_KEY]'),
    
    # Connection strings with passwords
    (r'(?i)(?:postgres|mysql|mongodb)(?:\+\w+)?://[^:]+:[^@]+@[^\s]+', '[REDACTED:CONNECTION_STRING]'),
    
    # Slack tokens
    (r'xox[baprs]-[a-zA-Z0-9-]{10,}', '[REDACTED:SLACK_TOKEN]'),
    
    # Stripe keys
    (r'sk_(?:live|test)_[a-zA-Z0-9]{24,}', '[REDACTED:STRIPE_KEY]'),
    (r'pk_(?:live|test)_[a-zA-Z0-9]{24,}', '[REDACTED:STRIPE_PUBLIC_KEY]'),
    
    # SendGrid
    (r'SG\.[a-zA-Z0-9_-]{22}\.[a-zA-Z0-9_-]{43}', '[REDACTED:SENDGRID_KEY]'),
    
    # Twilio
    (r'SK[a-f0-9]{32}', '[REDACTED:TWILIO_KEY]'),
    
    # Discord tokens
    (r'[MN][A-Za-z\d]{23,}\.[\w-]{6}\.[\w-]{27}', '[REDACTED:DISCORD_TOKEN]'),
    
    # npm tokens
    (r'npm_[a-zA-Z0-9]{36}', '[REDACTED:NPM_TOKEN]'),
    
    # PyPI tokens
    (r'pypi-AgEIcHlwaS5vcmc[a-zA-Z0-9_-]{50,}', '[REDACTED:PYPI_TOKEN]'),
]

# Compile patterns
COMPILED_PATTERNS = [(re.compile(pattern), replacement) for pattern, replacement in SECRET_PATTERNS]


def log_hook(level: str, message: str):
    """Log to hook log file."""
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(HOOK_LOG, "a") as f:
            f.write(f"{timestamp} [SECRETS-OUTPUT][{level}] {message}\n")
    except Exception:
        pass


def redact_secrets(text: str) -> tuple[str, int]:
    """Count the secret shapes in text. Returns (masked_text, count).

    Each match is masked before the next pattern runs, so a value that two
    patterns match is counted once. Only the count is used; the masked text is
    never shown to anyone.
    """
    if not text:
        return text, 0
    
    count = 0
    redacted = text
    
    for pattern, replacement in COMPILED_PATTERNS:
        matches = pattern.findall(redacted)
        if matches:
            count += len(matches)
            redacted = pattern.sub(replacement, redacted)
    
    return redacted, count


def _output_text(tool_result) -> str:
    """The text to scan, whichever shape Claude Code sent the tool result in."""
    if isinstance(tool_result, str):
        return tool_result
    if not isinstance(tool_result, dict):
        return ""
    text = tool_result.get("stdout") or tool_result.get("content") or tool_result.get("output")
    if isinstance(text, str) and text:
        return text
    # No text field, or one that is not a string (a list of content blocks):
    # scan the whole result as JSON rather than crash on it.
    return json.dumps(tool_result)


def _warn(count: int, tool_name: str) -> None:
    """Warn the model (additionalContext) and the operator (systemMessage)."""
    log_hook("DETECT", f"Detected {count} possible secret(s) in {tool_name} output")
    emit(posttool_context(
        f"[SECRETS DETECTED] {count} possible secret(s) appeared in this {tool_name} "
        "output. Do not repeat, quote or commit these values. Refer to a secret by "
        "its name, and read it from the environment when it is needed.",
        system_message=(
            f"[SECRETS DETECTED] {count} possible secret(s) in {tool_name} output; "
            "the model was told not to repeat them."
        ),
    ))


def main():
    input_data = parse_hook_input()
    if not isinstance(input_data, dict) or not input_data:
        # Claude Code always sends a payload, so an empty or unreadable one is an
        # anomaly worth a log line, never a silent return (April audit task C3).
        log_hook("WARN", "empty or unreadable hook payload; no output was scanned")
        respond()
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name not in CHECKED_TOOLS:
        respond()
        return

    _, count = redact_secrets(_output_text(input_data.get("tool_response", {})))
    if count > 0:
        _warn(count, tool_name)
    else:
        respond()


if __name__ == "__main__":
    main()
