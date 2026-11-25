#!/usr/bin/env bash
# TUI Detection Helper for GRIP
# Detects whether running in Claude Code TUI (terminal) vs IDE (VS Code/Cursor/JetBrains)
#
# Usage:
#   source "$HOME/.claude/lib/tui_detection.sh"
#   if is_tui; then echo "Terminal mode"; fi

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

is_tui() {
    # If VS Code, Cursor, or JetBrains detected, NOT TUI (IDE has widget rendering)
    [ -n "${VSCODE_PID:-}" ] && return 1
    [ -n "${VSCODE_GIT_ASKPASS_NODE:-}" ] && return 1
    [ -n "${CURSOR_SESSION_ID:-}" ] && return 1
    [ -n "${JETBRAINS_IDE:-}" ] && return 1
    [ -n "${INTELLIJ_ENVIRONMENT_READER:-}" ] && return 1

    # Check GRIP config for explicit override
    if [ -f "$CLAUDE_DIR/config/grip.json" ]; then
        local tui_val
        tui_val=$(jq -r '.tui_mode // "auto"' "$CLAUDE_DIR/config/grip.json" 2>/dev/null)
        [ "$tui_val" = "true" ] && return 0
        [ "$tui_val" = "false" ] && return 1
    fi

    # Default: assume TUI (Claude Code CLI is most common surface)
    return 0
}

# Return surface type as string
get_surface() {
    if is_tui; then
        echo "tui"
    else
        echo "ide"
    fi
}
