#!/usr/bin/env python3
"""E13: ANSI env-var suppressor — PreToolUse hook for Bash.

Prepends NO_COLOR=1 FORCE_COLOR=0 CLICOLOR=0 to Bash commands so ANSI
escape codes are never generated at source. More efficient than post-hoc
stripping — RTK TOML strip_ansi remains the fallback if CC ignores this.

Hook position: PreToolUse[Bash] — before RTK hook at position 11.
Estimated savings: ~50K tokens/month (varies by tool mix).

If Claude Code does not support tool-input mutation from Python PreToolUse
hooks, this hook exits 0 silently (safe no-op). RTK TOML strip_ansi covers
the fallback for all GRIP-specific command patterns.
"""
from __future__ import annotations

import json
import sys

# Substrings that mark commands where colour is semantic or already suppressed.
_SKIP_IF_CONTAINS = (
    "pm2 list",
    "pm2 monit",
    "pm2 logs",
    " bat ",
    "\nbat ",
    "delta ",
    "glow ",
    "htop",
    "btop",
    "lazygit",
    "tig",
    "watch ",
    "fzf",
    "less ",
    "man ",
    "NO_COLOR",
    "FORCE_COLOR",
    "CLICOLOR",
    "--color",
    "-t ",       # ssh -t (PTY allocation)
)

# Complex shell constructs where prepending env vars may not propagate cleanly.
_SKIP_PREFIXES = ("(", "{ ", "eval ", "exec ")


def _should_suppress(command: str) -> bool:
    stripped = command.lstrip()
    if any(stripped.startswith(p) for p in _SKIP_PREFIXES):
        return False
    return not any(tok in command for tok in _SKIP_IF_CONTAINS)


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if hook_input.get("tool_name") != "Bash":
        sys.exit(0)

    command: str = hook_input.get("tool_input", {}).get("command", "")
    if not command or not _should_suppress(command):
        sys.exit(0)

    print(json.dumps({"tool_input": {"command": f"NO_COLOR=1 FORCE_COLOR=0 CLICOLOR=0 {command}"}}))


if __name__ == "__main__":
    main()
