#!/usr/bin/env bash
#
# GRIP Auto Library
# Shared functions for automatic instance preservation
#
# PURPOSE:
#   Provides functions for session hooks to:
#   1. Auto-serialize instances at session end
#   2. Auto-resurrect instances at session start
#   3. Check for existing project instances
#
# USAGE:
#   source ~/.claude/lib/grip-auto.sh
#   grip_auto_resurrect
#   grip_auto_serialize "Achievement description"
#
# VERSION: 1.0.0
# DATE: 2025-12-08
#

set -euo pipefail

[[ -z "${CLAUDE_DIR:-}" ]] && readonly CLAUDE_DIR="$HOME/.claude"
[[ -z "${LIB_DIR:-}" ]] && readonly LIB_DIR="$CLAUDE_DIR/lib"

grip_check_instance() {
    python3 "$LIB_DIR/instance-resurrector.py" check 2>/dev/null
}

grip_auto_resurrect() {
    local result
    result=$(python3 "$LIB_DIR/instance-resurrector.py" auto 2>/dev/null) || return 1

    if [[ -n "$result" ]]; then
        echo "$result"
        return 0
    fi
    return 1
}

grip_auto_serialize() {
    local achievement="${1:-Auto-serialized at session end}"
    local instance_id

    instance_id=$(python3 "$LIB_DIR/instance-serializer.py" auto \
        --achievement "$achievement" 2>/dev/null) || return 1

    if [[ -n "$instance_id" ]]; then
        echo "$instance_id"
        return 0
    fi
    return 1
}

grip_has_project_instance() {
    local check_result
    check_result=$(grip_check_instance 2>/dev/null) || return 1

    [[ "$check_result" == found:* ]]
}

export -f grip_check_instance 2>/dev/null || true
export -f grip_auto_resurrect 2>/dev/null || true
export -f grip_auto_serialize 2>/dev/null || true
export -f grip_has_project_instance 2>/dev/null || true

if [[ "${BASH_SOURCE[0]:-$0}" == "${0}" ]] && [[ -n "$1" ]]; then
    case "${1:-check}" in
        check)
            grip_check_instance
            ;;
        resurrect)
            grip_auto_resurrect
            ;;
        serialize)
            grip_auto_serialize "${2:-Manual serialization}"
            ;;
        *)
            echo "Usage: $0 {check|resurrect|serialize [achievement]}"
            exit 1
            ;;
    esac
fi
