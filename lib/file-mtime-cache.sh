#!/usr/bin/env bash
set -o pipefail
#
# File Modification Time Cache Library
# Tracks file mtimes to prevent redundant reads across sessions
#
# PURPOSE:
#   This library provides functions to:
#   1. Get file modification times (cross-platform)
#   2. Check if files changed since last cache
#   3. Update mtime cache
#
# INTEGRATION:
#   Source from hooks/session-start.sh for state file tracking
#
# VERSION: 1.0.0
# DATE: 2025-12-12
#

# ============================================================================
# CONSTANTS
# ============================================================================

[[ -z "${CLAUDE_DIR:-}" ]] && readonly CLAUDE_DIR="$HOME/.claude"
[[ -z "${MTIME_CACHE:-}" ]] && readonly MTIME_CACHE="$CLAUDE_DIR/cache/file-mtimes.json"

# ============================================================================
# CORE FUNCTIONS
# ============================================================================

# Get file mtime (cross-platform: macOS, Linux, Windows Git Bash)
get_file_mtime() {
    local file="$1"
    [[ ! -f "$file" ]] && return 1

    local uname_out
    uname_out="$(uname 2>/dev/null)"
    if [[ "$uname_out" == "Darwin" ]]; then
        # macOS: BSD stat
        stat -f %m "$file" 2>/dev/null
    elif [[ "$uname_out" == MINGW* || "$uname_out" == CYGWIN* || "$uname_out" == MSYS* ]]; then
        # Windows Git Bash / MSYS2: GNU stat available via mingw-w64
        stat -c %Y "$file" 2>/dev/null \
            || python3 -c "import os; print(int(os.path.getmtime('$file')))" 2>/dev/null
    else
        # Linux / WSL: GNU stat
        stat -c %Y "$file" 2>/dev/null
    fi
}

# Check if file changed since last cache
# Returns 0 (true) if changed or no cache exists
# Returns 1 (false) if unchanged
file_changed_since_cache() {
    local file="$1"
    [[ ! -f "$file" ]] && return 0  # File doesn't exist = treat as changed
    [[ ! -f "$MTIME_CACHE" ]] && return 0  # No cache = assume changed

    local cached_mtime current_mtime
    cached_mtime=$(jq -r ".files[\"$file\"].mtime // 0" "$MTIME_CACHE" 2>/dev/null) || return 0
    current_mtime=$(get_file_mtime "$file") || return 0

    # If current mtime is greater than cached, file changed
    [[ "$current_mtime" -gt "$cached_mtime" ]]
}

# Update cache for a file
update_mtime_cache() {
    local file="$1"
    [[ ! -f "$file" ]] && return 1

    local current_mtime
    current_mtime=$(get_file_mtime "$file") || return 1

    mkdir -p "$(dirname "$MTIME_CACHE")"

    if [[ ! -f "$MTIME_CACHE" ]]; then
        echo '{"files":{}}' > "$MTIME_CACHE"
    fi

    local tmp_file
    tmp_file=$(mktemp)
    jq --arg file "$file" \
       --arg mtime "$current_mtime" \
       --arg checked "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
       '.files[$file] = {mtime: ($mtime | tonumber), last_checked: $checked}' \
       "$MTIME_CACHE" > "$tmp_file" && mv "$tmp_file" "$MTIME_CACHE"
}

# Get last checked time for a file
get_last_checked() {
    local file="$1"
    [[ ! -f "$MTIME_CACHE" ]] && return 1

    jq -r ".files[\"$file\"].last_checked // empty" "$MTIME_CACHE" 2>/dev/null
}

# Clear mtime cache (cross-platform: avoid rm for Windows Gate 5 compatibility)
clear_mtime_cache() {
    [[ -f "$MTIME_CACHE" ]] && python3 -c "import os; os.unlink('$MTIME_CACHE')" 2>/dev/null || true
}

# Export functions for subshells
export -f get_file_mtime file_changed_since_cache update_mtime_cache get_last_checked clear_mtime_cache
