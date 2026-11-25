"""Shared utilities for GRIP PostToolUse hooks.

PostToolUse hooks receive JSON on stdin with tool_name, tool_input, and
tool_response fields. They return JSON on stdout. Unlike PreToolUse hooks,
PostToolUse hooks do NOT use a "decision" field - returning an empty {}
implicitly allows. A "systemMessage" is shown to the operator and does not
reach the model (measured 2026-11-24, GRIP#9497). Text the model must read
goes in hookSpecificOutput.additionalContext: build it with
lib/hook_response.posttool_context.
"""

__all__ = ["parse_hook_input", "respond", "extract_tool_info"]

import json
import sys


def parse_hook_input() -> dict:
    """Parse hook JSON input from stdin.

    Returns:
        Parsed dict, or empty dict if stdin is empty or contains invalid JSON.
    """
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def respond(system_message: str = None) -> None:
    """Print a PostToolUse JSON response to stdout.

    An empty {} means "nothing to say". A ``system_message`` is shown to the
    operator only: it does not reach the model (measured 2026-11-24,
    GRIP#9497). For text the model must read, print
    lib/hook_response.posttool_context instead.

    Args:
        system_message: Optional line shown to the operator.
    """
    response = {}
    if system_message:
        response["systemMessage"] = system_message
    print(json.dumps(response))


def extract_tool_info(hook_input: dict) -> tuple[str, dict, str]:
    """Extract tool execution details from hook input.

    Args:
        hook_input: Parsed hook JSON input.

    Returns:
        Tuple of (tool_name, tool_input, tool_response).
    """
    return (
        hook_input.get("tool_name", ""),
        hook_input.get("tool_input", {}),
        str(hook_input.get("tool_response", "")),
    )
