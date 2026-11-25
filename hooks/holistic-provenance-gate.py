#!/usr/bin/env python3
"""P2 W0 — PreToolUse DENY gate for /holistic provenance.

While a `/holistic` provenance marker is ACTIVE
(lib/gates/holistic_provenance.read_provenance() not None), DENY any Bash
command that is a git mutation (`git … commit|push|checkout|switch|reset
|rebase`) OR invokes `closeout`/`closeout.py`. `/holistic` provably needs
none of those (it only regenerates a file, writes its marker, dispatches a
bg agent) — so denying ALL git-mutation + closeout under its provenance is
total, simple, unbypassable.

PARAMOUNT, NOT yolo-relaxable (mirrors hooks/guardrail-evasion-gate.py:
evading a cross-session-safety gate is never the autonomous fast-path —
this hook does NOT consult the yolo marker).

Fail-safe: marker absent/stale/corrupt ⇒ read_provenance() is None ⇒
this hook ALLOWS (a normal session's git is never touched). The gate only
restricts WHILE `/holistic` explicitly runs.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

_CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
sys.path.insert(0, str(_CLAUDE_DIR))  # resolve lib.gates.* like sibling hooks
_AUDIT = _CLAUDE_DIR / "logs" / "holistic-provenance-gate.jsonl"

# git mutation subcommands + closeout — denied while provenance active.
_GIT_MUT = re.compile(
    r"\bgit\b[^|;&\n]*\b(commit|push|checkout|switch|reset|rebase)\b"
)
_CLOSEOUT = re.compile(r"\bcloseout(?:\.py)?\b")


def _read_payload() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _emit_deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


def _audit(command: str, reason: str) -> None:
    try:
        _AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT.open("a") as fh:
            fh.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "decision": "deny", "command": command[:300], "reason": reason,
            }) + "\n")
    except Exception:
        pass  # audit best-effort; never let logging block the deny decision


def main() -> int:
    payload = _read_payload()
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    if tool_name != "Bash":
        return 0  # only gate Bash
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    command = (tool_input.get("command") or "")

    try:
        from lib.gates.holistic_provenance import read_provenance
    except Exception:
        return 0  # gate module unimportable ⇒ fail-safe ALLOW (no false block)

    if read_provenance() is None:
        return 0  # provenance INACTIVE ⇒ normal session, allow everything

    # Provenance ACTIVE: /holistic is running. Deny git-mutation + closeout.
    if _GIT_MUT.search(command):
        reason = ("BLOCKED by /holistic provenance gate: git mutation "
                  "(commit/push/checkout/switch/reset/rebase) is forbidden "
                  "while /holistic runs. /holistic never mutates git — a "
                  "cross-session foreign mutation here is exactly the hazard "
                  "this gate prevents. PARAMOUNT, not yolo-relaxable.")
        _audit(command, "git-mutation")
        _emit_deny(reason)
        return 0
    if _CLOSEOUT.search(command):
        reason = ("BLOCKED by /holistic provenance gate: closeout is "
                  "forbidden while /holistic runs (it would mutate git / "
                  "session state cross-session). PARAMOUNT, not "
                  "yolo-relaxable.")
        _audit(command, "closeout")
        _emit_deny(reason)
        return 0
    return 0  # any other Bash is allowed even under provenance


if __name__ == "__main__":
    raise SystemExit(main())
