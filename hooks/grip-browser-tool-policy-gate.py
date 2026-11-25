#!/usr/bin/env python3
"""
grip-browser Tool Policy Gate -- PreToolUse Hook
================================================
Capability lock for stealth-browser-mcp, attached opt-in per launch as the
stdio MCP server ``grip-browser`` (``claude --mcp-config
mcp-servers/grip-browser/mcp.json`` -> ``mcp-servers/grip-browser/start.sh``).

Why a gate exists at all: the server renders pages the operator did not write,
and a page can carry instructions aimed at the agent. Some of the server's 101
tools run code in the page or on the host, read cookies and storage, or write
host paths with no allowlist upstream. ``rules/untrusted-content-capability-lock.md``
requires an allowlist whose unknown names degrade to fewer capabilities; in an
autonomous loop the permission prompt is bypassed, so the prompt layer is no
control there.

Why the gate is narrow: ``rules/full-power-stop-override.md``. Reading the page
and acting on it in a fresh, ephemeral identity pass untouched; the lock sits
on credentials, code execution, host files and server administration, each
behind one operator-owned switch set for the launch that needs it, and on the
arguments that decide a tool's effect (``spawn_browser`` pinned,
``navigate`` held to http(s) on a public host, every path under the artefact
root after realpath).

Gate logic (single responsibility):
  1. Fire only on tool names starting with ``mcp__grip-browser__``
  2. Build the context from the environment and the private configuration
     (lib/grip_browser_tool_policy.context_from_env)
  3. Evaluate with lib/grip_browser_tool_policy.py: observe -> allow;
     interact -> allow in ephemeral mode, else GRIP_BROWSER_ALLOW_INTERACT=1;
     credential / code-exec / host-file -> allow iff their switch is "1";
     server-admin -> DENY; unknown -> DENY; a refusing or RAISING argument
     rule -> DENY
  4. Every deny is appended to logs/grip-browser-tool-policy-gate.jsonl (the
     H-D measurement: how often a read task needs a denied capability);
     the log write is fail-open
  5. Unreadable payload -> DENY. The matcher routes only ``mcp__grip-browser__*``
     calls here, so a payload this hook cannot parse means the harness changed
     its event shape under a capability lock; the registry declares
     ``failure_mode: closed`` and the binding carries ``onFailure: block`` for
     the same reason. The fix -- Edit this hook -- lies outside the denied set.

Escape hatch (rules/gate-resolution-escape-hatch.md, Invariant 1): the denied
set is ``mcp__grip-browser__*`` only. Every resolution -- re-launch with the
switch, classify the tool in lib/grip_browser_tool_policy.py with Edit, put the
file under the artefact root, or read the page with WebFetch instead -- lies
outside it, so the gate cannot block its own fix. Not yolo-relaxable: an
autonomous loop is the actor most likely to be steered by page content.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.hook_response import emit, pretool_decision  # noqa: E402
from lib.grip_browser_tool_policy import (  # noqa: E402
    PREFIX,
    context_from_env,
    evaluate as evaluate_policy,
)

AUDIT_LOG_ENV = "GRIP_BROWSER_GATE_AUDIT_LOG"
_DEFAULT_AUDIT_LOG = Path(__file__).resolve().parent.parent / "logs" / "grip-browser-tool-policy-gate.jsonl"


def _audit_log_path() -> Path:
    override = (os.environ.get(AUDIT_LOG_ENV) or "").strip()
    return Path(override) if override else _DEFAULT_AUDIT_LOG


def evaluate(tool_name, tool_input, environ):
    """Return a deny reason for a grip-browser tool call the policy refuses, else None."""
    if not isinstance(tool_name, str) or not tool_name.startswith(PREFIX):
        return None
    context = context_from_env(environ)
    verdict = evaluate_policy(tool_name, tool_input or {}, context)
    if verdict.allowed:
        return None
    _audit(verdict, tool_input)
    return verdict.reason


def _audit(verdict, tool_input) -> None:
    """One line per deny, fail-open: the gate must never fail because a log did."""
    try:
        log = _audit_log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        keys = sorted((tool_input or {}).keys()) if isinstance(tool_input, dict) else []
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "tool": verdict.tool,
                "class": verdict.tool_class,
                "argument_keys": keys,
                "reason": verdict.reason[:240],
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


UNREADABLE_REASON = (
    "[GRIP] grip-browser tool policy gate could not read the PreToolUse payload, so the "
    "call is refused (a capability lock fails closed). Fix the payload shape or the hook "
    "(hooks/grip-browser-tool-policy-gate.py); Edit is not gated here."
)


def _read_event():
    """The PreToolUse event as a dict, or None when stdin is not one."""
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        return None
    return event if isinstance(event, dict) else None


def main():
    event = _read_event()
    if event is None:
        emit(pretool_decision("deny", UNREADABLE_REASON))
        sys.exit(0)
    reason = evaluate(event.get("tool_name"), event.get("tool_input"), os.environ)
    if reason:
        emit(pretool_decision("deny", reason))
        sys.exit(0)


if __name__ == "__main__":
    main()
