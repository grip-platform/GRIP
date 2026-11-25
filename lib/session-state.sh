#!/usr/bin/env bash
set -o pipefail
#
# Session State Library
# State checkpoint functions via instance JSON next_up field
#
# State is now stored in instance JSON (next_up field),
# not flat markdown files. This library provides backward-compatible
# function signatures that no-op or delegate to the serializer.
#
# VERSION: 2.0.0
# DATE: 2026-02-07
#

# ============================================================================
# CONSTANTS
# ============================================================================

[[ -z "${CLAUDE_DIR:-}" ]] && readonly CLAUDE_DIR="$HOME/.claude"

# ============================================================================
# CORE FUNCTIONS
# ============================================================================

# Save session checkpoint - now handled by instance serialization
save_session_checkpoint() {
    # State saved via instance serializer next_up field
    # This function is kept for backward compatibility
    return 0
}

# Get state file for current project - deprecated
get_state_file() {
    # State stored in instance JSON, not flat files
    return 1
}

# Check if state file exists - deprecated
has_state_file() {
    return 1
}

# Export functions
export -f save_session_checkpoint get_state_file has_state_file
