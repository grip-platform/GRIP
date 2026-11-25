#!/usr/bin/env python3
"""PreToolUse hook: classify principal of inbound `<channel>` blocks.

When the relay-listener sees a `<channel ...>...</channel>` block in the
conversation context (delivered by grip-channel daemon), classify the
sender and inject a context marker so the model treats V>> as direct
steering (not "external untrusted data").

The hook is intentionally informational, NOT a deny gate. It writes an
`additionalContext` line that the LLM consumes, of the form:

    [PRINCIPAL=operator|teammate|external] sender=<id> platform=<...>

Source of truth: ``lib/relay_principal_classifier.py`` (library does the
classification; this hook is a thin shim per syscall doctrine).

Trigger matcher: PreToolUse on ``mcp__grip-channel__channel_*`` so the
hook only fires on relay-relevant actions.

Hypothesis: H-PRINCIPAL-1 (registered 2026-05-13).
Audit log:  ~/.claude/logs/principal-classifier.jsonl
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_CLAUDE_DIR = Path.home() / ".claude"
sys.path.insert(0, str(_CLAUDE_DIR))

from lib.relay_principal_classifier import (  # noqa: E402
    classify_inbound_text,
    classify_principal,
)

_AUDIT_LOG = _CLAUDE_DIR / "logs" / "principal-classifier.jsonl"


def _read_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _audit(record: dict) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with _AUDIT_LOG.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _extract_inbound_text(payload: dict) -> str:
    """Pull the most recent inbound channel text from the harness payload.

    Harness shapes vary across CC versions. Try common paths.
    """
    for key in ("inbound", "prompt", "user_prompt", "context"):
        val = payload.get(key)
        if isinstance(val, str) and "<channel" in val:
            return val
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if isinstance(tool_input, dict):
        for v in tool_input.values():
            if isinstance(v, str) and "<channel" in v:
                return v
    return ""


def main() -> int:
    payload = _read_stdin()
    inbound = _extract_inbound_text(payload)

    # Two classification paths: (1) inbound text contains a fresh <channel>,
    # or (2) the tool_input carries an explicit sender field we can classify.
    if inbound:
        verdict = classify_inbound_text(inbound)
    else:
        tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
        sender = (
            tool_input.get("senderId")
            or tool_input.get("sender")
            or tool_input.get("recipient")  # outbound reply target as fallback
            or ""
        )
        verdict = classify_principal(sender, tool_input.get("platform", ""))

    _audit({
        "principal": verdict.principal,
        "sender": verdict.sender,
        "platform": verdict.platform,
        "tool": payload.get("tool_name") or payload.get("toolName") or "",
    })

    # Inject a context marker. Exit 0 + additionalContext = informational.
    marker = (
        f"[PRINCIPAL={verdict.principal}] "
        f"sender={verdict.sender or 'unknown'} "
        f"platform={verdict.platform or 'unknown'} - {verdict.reason}"
    )
    print(json.dumps({
        "hookEventName": "PreToolUse",
        "additionalContext": marker,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
