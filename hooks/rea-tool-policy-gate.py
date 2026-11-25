#!/usr/bin/env python3
"""
REA Tool Policy Gate -- PreToolUse Hook
=======================================
Capability lock for the REA reverse-engineering MCP server that GRIP attaches
to every session (``.mcp.json`` -> ``mcp-servers/rea/start.sh``).

Why a gate exists at all: REA's tools analyse UNTRUSTED content, and a few of
them run that content -- a captured process scenario executes the target, a
browser or Electron scenario opens it, a native-UI observation screenshots a
live window, an extraction writes an untrusted archive's contents to disk.
Strings inside a hostile binary can steer the agent toward exactly those
calls. ``rules/untrusted-content-capability-lock.md`` requires an allowlist
whose unknown names degrade to fewer capabilities. In an autonomous loop the
permission prompt is bypassed, so the prompt layer is no control there.

Why the gate is narrow: ``rules/full-power-stop-override.md``. Inspection
tools read bytes the agent could read with ``cat``; they pass untouched. The
lock sits on execution and bulk writes, and it is one operator-owned switch,
``GRIP_REA_ALLOW_RUNTIME=1``, set for the launch that needs it.

Gate logic (single responsibility):
  1. Fire only on tool names starting with ``mcp__rea__``
  2. Classify via lib/rea_tool_policy.py (read / runtime / unknown)
  3. read     -> allow silently
  4. runtime  -> allow iff GRIP_REA_ALLOW_RUNTIME == "1", else DENY
  5. unknown  -> DENY (an unclassified tool is a newer REA than the policy
     knows; classify it, never widen by default)
  6. Unreadable payload -> DENY. The matcher routes only ``mcp__rea__*`` calls
     here, so a payload this hook cannot parse means the harness changed its
     event shape under a capability lock; the registry declares
     ``failure_mode: closed`` and the binding carries ``onFailure: block`` for
     the same reason (a timed-out or crashed hook must not let a runtime tool
     through). The fix -- Edit this hook -- lies outside the denied set.

Escape hatch (rules/gate-resolution-escape-hatch.md, Invariant 1): the
denied set is ``mcp__rea__*`` only. Every resolution -- re-launch with the
switch, classify the tool in lib/rea_tool_policy.py with Edit, or inspect the
target with a non-REA tool -- lies outside it, so the gate cannot block its
own fix. Not yolo-relaxable: an autonomous loop is the actor most likely to
be steered by hostile content.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.hook_response import emit, pretool_decision  # noqa: E402
from lib.rea_tool_policy import (  # noqa: E402
    PREFIX,
    allow_runtime_from_env,
    deny_reason,
    is_legal_tool_call,
)


def evaluate(tool_name, tool_input, environ):
    """Return a deny reason for a REA tool call the policy refuses, else None."""
    if not isinstance(tool_name, str) or not tool_name.startswith(PREFIX):
        return None
    context = {"allow_runtime": allow_runtime_from_env(environ)}
    if is_legal_tool_call(tool_name, tool_input or {}, context):
        return None
    return deny_reason(tool_name)


UNREADABLE_REASON = (
    "[GRIP] REA tool policy gate could not read the PreToolUse payload, so the "
    "call is refused (a capability lock fails closed). Fix the payload shape "
    "or the hook (hooks/rea-tool-policy-gate.py); Edit is not gated here."
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
