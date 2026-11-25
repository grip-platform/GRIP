#!/bin/bash
#
# Capability Detector Library
# Detects extended thinking capability (MAX subscription)
#
# Priority order:
# 1. GRIP_EXTENDED_THINKING env var (highest)
# 2. ~/.claude/config/grip.json config file
# 3. ~/.claude/.extended-thinking marker file
#
# VERSION: 1.0.0
# DATE: 2026-01-22

# Detect if extended thinking is enabled
# Returns 0 if enabled, 1 if disabled
is_extended_thinking_enabled() {
    # Priority 1: Explicit environment variable (highest priority)
    if [[ "${GRIP_EXTENDED_THINKING:-}" == "1" ]]; then
        return 0
    fi
    if [[ "${GRIP_EXTENDED_THINKING:-}" == "0" ]]; then
        return 1
    fi

    # Priority 2: Config file
    local config="$HOME/.claude/config/grip.json"
    if [[ -f "$config" ]]; then
        local enabled
        enabled=$(jq -r '.extended_thinking // false' "$config" 2>/dev/null)
        [[ "$enabled" == "true" ]] && return 0
    fi

    # Priority 3: Marker file (set by onboarding)
    [[ -f "$HOME/.claude/.extended-thinking" ]] && return 0

    return 1
}

# Get the source of the extended thinking setting
# Returns: "env", "config", "marker", or "none"
get_extended_thinking_source() {
    if [[ "${GRIP_EXTENDED_THINKING:-}" == "1" ]]; then
        echo "env"
        return
    fi
    if [[ "${GRIP_EXTENDED_THINKING:-}" == "0" ]]; then
        echo "none"
        return
    fi

    local config="$HOME/.claude/config/grip.json"
    if [[ -f "$config" ]]; then
        local enabled
        enabled=$(jq -r '.extended_thinking // false' "$config" 2>/dev/null)
        if [[ "$enabled" == "true" ]]; then
            echo "config"
            return
        fi
    fi

    if [[ -f "$HOME/.claude/.extended-thinking" ]]; then
        echo "marker"
        return
    fi

    echo "none"
}

# Enable extended thinking by creating marker file
enable_extended_thinking() {
    touch "$HOME/.claude/.extended-thinking"
    echo "Extended thinking enabled via marker file"
}

# Disable extended thinking by removing marker file
disable_extended_thinking() {
    rm -f "$HOME/.claude/.extended-thinking"
    echo "Extended thinking disabled (marker file removed)"
}
