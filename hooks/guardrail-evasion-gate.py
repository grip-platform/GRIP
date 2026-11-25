#!/usr/bin/env python3
"""PreToolUse hook: DENY Write/Edit that obfuscates code to evade a safety scanner.

Path-agnostic (the originating incident was in nexus-poc, not ~/.claude).
No bypass — evading a safety control is never legitimate; fix the root cause.

Detector: lib/guardrail_evasion_detector.py
Tests:    tests/test_guardrail_evasion_detector.py
Audit:    ~/.claude/logs/guardrail-evasion-gate.jsonl
Rule:     rules/no-guardrail-evasion.md
Origin:   2026-05-15 — W3 agent wrote `"IDR_SIGNING" + "_KEY"` with the
          comment "to avoid guardrail substring scan". Caught, rejected,
          mechanically enforced at operator direction.
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

from lib.guardrail_evasion_llm import combined_evaluate as evaluate  # noqa: E402
from lib.hook_response import emit, pretool_decision  # noqa: E402

_AUDIT_LOG = _CLAUDE_DIR / "logs" / "guardrail-evasion-gate.jsonl"
_GATED_TOOLS = {"Write", "Edit"}


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


def _extract(tool_name: str, tool_input: dict) -> tuple[str, str] | None:
    if tool_name == "Write":
        return (tool_input.get("content", "") or "", tool_input.get("file_path", ""))
    if tool_name == "Edit":
        return (tool_input.get("new_string", "") or "", tool_input.get("file_path", ""))
    return None


def _emit_deny(reason: str) -> None:
    emit(pretool_decision("deny", reason))
    sys.exit(0)


def main() -> int:
    payload = _read_stdin()
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    if tool_name not in _GATED_TOOLS:
        return 0
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    extracted = _extract(tool_name, tool_input)
    if extracted is None:
        return 0
    content, target = extracted
    verdict = evaluate(content, target)
    if not verdict.allowed:
        _audit({
            "decision": "deny", "tool": tool_name, "target": target,
            "signal": verdict.signal, "evidence": verdict.evidence,
        })
        _emit_deny(verdict.reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
