#!/usr/bin/env python3
"""PreToolUse hook: clean-room context firewall for relay outbound.

Fires on grip-channel outbound tool calls. When the destination channel's
scope differs from in-context references found in the recent transcript,
inject a `[CONTEXT FIREWALL]` advisory so the model composes the reply
from the inbound + canonical rules, NOT from stale prior-session work.

This hook is ADVISORY (not deny). The hard deny path for outbound scope
mismatch is owned by ``hooks/channel-scope-gate.py`` which actually
blocks the post when the OUTBOUND message body itself carries off-scope
content. This firewall hook protects against the upstream case where the
outbound body LOOKS clean but the session context behind it is dirty —
which is exactly how the 2026-05-13 bad-GRIP incident composed its post.

Tests:    tests/test_relay_context_firewall.py
Audit:    ~/.claude/logs/relay-context-firewall.jsonl
Hypothesis: H-NULL-CONTEXT-RELAY-1 (registered 2026-05-13).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_CLAUDE_DIR = Path.home() / ".claude"
# Code is imported from the tree this hook lives in, never from ~/.claude:
# where ~/.claude is a stale copy rather than a link to the clone (a Windows
# teammate's layout, where Python reads the home folder from USERPROFILE),
# importing from it crashed this gate before it could check anything
# (fleet report 2026-10-02, B-2). Data paths are unchanged.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.relay_context_firewall import evaluate_firewall  # noqa: E402
from lib.channel_scope_gate import GATED_TOOLS, extract_message_and_target  # noqa: E402

_AUDIT_LOG = _CLAUDE_DIR / "logs" / "relay-context-firewall.jsonl"

# Cap on how much transcript text we scan. Larger windows catch more bleed
# but cost token / IO. 8 KB is enough to catch the recent few turns.
_TRANSCRIPT_SCAN_CAP_BYTES = 8 * 1024


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


def _read_transcript_tail(transcript_path: str) -> str:
    """Read the last ``_TRANSCRIPT_SCAN_CAP_BYTES`` of the transcript file.

    Returns empty string on any failure (missing file, permissions, etc.)
    — the firewall fails-open (no risk flag) rather than blocking.
    """
    if not transcript_path:
        return ""
    try:
        path = Path(transcript_path)
        if not path.exists():
            return ""
        size = path.stat().st_size
        offset = max(0, size - _TRANSCRIPT_SCAN_CAP_BYTES)
        with path.open("rb") as fh:
            fh.seek(offset)
            return fh.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def main() -> int:
    payload = _read_stdin()
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    if tool_name not in GATED_TOOLS:
        return 0
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    extracted = extract_message_and_target(tool_name, tool_input)
    if extracted is None:
        return 0
    _, destination = extracted

    transcript_path = (
        payload.get("transcript_path")
        or payload.get("transcriptPath")
        or ""
    )
    transcript_text = _read_transcript_tail(transcript_path)
    verdict = evaluate_firewall(transcript_text, destination)

    _audit({
        "tool": tool_name,
        "destination": destination,
        "scope": verdict.destination_scope,
        "risk": verdict.risk,
        "offending": list(verdict.offending_scopes),
    })

    if verdict.risk:
        marker = (
            "[CONTEXT FIREWALL] Scope='{scope}' channel={dest} but transcript "
            "contains off-scope references to {bleed}. Compose the reply from "
            "the INBOUND <channel> block and canonical channel rules ONLY. "
            "Do NOT reference the off-scope topics in your reply. Per "
            "feedback_vps_only_inbound_relay_architecture.md and "
            "feedback_channel_scope_isolation_paramount.md."
        ).format(
            scope=verdict.destination_scope,
            dest=destination,
            bleed=", ".join(verdict.offending_scopes),
        )
        print(json.dumps({
            "hookEventName": "PreToolUse",
            "additionalContext": marker,
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
