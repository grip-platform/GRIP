#!/usr/bin/env bash
# Shared logging functions for GRIP shell scripts.
#
# Consolidates logging patterns to eliminate DRY violations.
# All shell scripts should source this instead of defining their own.
#
# Usage:
#   source "${CLAUDE_DIR:-$HOME/.claude}/lib/logging.sh"
#
#   # Console output with colours:
#   log_info "Message here"
#   log_success "Operation completed"
#   log_error "Something failed"
#
#   # File logging (writes to .hooks.log):
#   grip_log "my-tag" "INFO" "Message here"

# Guard against double-sourcing
if [[ -n "${_GRIP_LOGGING_LOADED:-}" ]]; then
    return 0 2>/dev/null || true
fi
_GRIP_LOGGING_LOADED=1

# Default log file location
# One hook log, shared with the Python side (lib/state_paths.py HOOK_LOG).
# Nothing is created at source time: scripts/install.sh sources this file
# before it clones into ~/.claude, and a logs/ directory made here would make
# the installer refuse ("~/.claude already exists"), which
# tests/test_gitattributes_drivers_defined.py caught on GRIP#8470.
GRIP_LOG_FILE="${GRIP_LOG_FILE:-${CLAUDE_DIR:-$HOME/.claude}/logs/hooks.log}"

# =============================================================================
# ANSI Colour definitions
# =============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Colour

# =============================================================================
# Console output functions (coloured, for user feedback)
# =============================================================================
log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_pass()    { echo -e "${GREEN}[PASS]${NC} $*"; }
log_fail()    { echo -e "${RED}[FAIL]${NC} $*"; }
log_step()    { echo -e "${BLUE}[STEP]${NC} $*"; }
log_dry()     { echo -e "${BLUE}[DRY-RUN]${NC} Would: $*"; }

# =============================================================================
# File logging functions (writes to GRIP_LOG_FILE)
# =============================================================================

# Core logging function
# Args: $1=tag, $2=level, $*=message
# The logs/ directory is created on the first write, and only under an install
# directory that already exists; the install directory itself is never created.
_grip_log_dir_ready() {
    local dir="${GRIP_LOG_FILE%/*}"
    [[ -d "$dir" ]] && return 0
    [[ -d "${dir%/*}" ]] && mkdir -p "$dir" 2>/dev/null
}

grip_log() {
    local tag="$1"
    local level="$2"
    shift 2
    _grip_log_dir_ready || return 0
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [$tag][$level] $*" >> "$GRIP_LOG_FILE"
}

# Convenience wrappers
grip_log_info()  { grip_log "$1" "INFO" "${@:2}"; }
grip_log_warn()  { grip_log "$1" "WARN" "${@:2}"; }
grip_log_error() { grip_log "$1" "ERROR" "${@:2}"; }
grip_log_debug() { grip_log "$1" "DEBUG" "${@:2}"; }

# =============================================================================
# Log rotation (call at session start)
# =============================================================================

# Rotate a log once it is larger than max_bytes (second argument; default
# GRIP_MAX_LOG_BYTES). The whole file is archived first, gzipped, under
# logs/archive/, which git ignores. Only when gzip has tested the archive sound
# is the live file trimmed to its newest lines, and the trim rewrites it in
# place, so a writer that holds it open (a launchd job's StandardOutPath) goes
# on writing to the same file. Nothing is deleted: on 2026-11-24 V>> chose to
# keep every archive (GRIP#9496, decision D3). Until then this function said it
# compressed the overflow into an archive, but it discarded it.
GRIP_MAX_LOG_BYTES="${GRIP_MAX_LOG_BYTES:-1048576}"  # 1MB default

grip_log_rotate() {
    local log_file="${1:-$GRIP_LOG_FILE}"
    local max_bytes="${2:-$GRIP_MAX_LOG_BYTES}"
    local archive_dir="${CLAUDE_DIR:-$HOME/.claude}/logs/archive"
    local keep_lines=2000

    [[ ! -f "$log_file" ]] && return 0

    local size
    # Cross-platform file size
    if [[ "$(uname)" == "Darwin" ]]; then
        size=$(stat -f %z "$log_file" 2>/dev/null) || return 0
    else
        size=$(stat -c %s "$log_file" 2>/dev/null) || return 0
    fi

    if (( size <= max_bytes )); then
        return 0
    fi

    mkdir -p "$archive_dir" || return 0

    # The PID keeps apart two sessions that start in the same second.
    local archive
    archive="${archive_dir}/$(basename "$log_file").$(date -u +%Y%m%dT%H%M%SZ).$$.gz"

    # Archive first. A failed or interrupted gzip leaves the live log as it was.
    if ! { gzip -c "$log_file" > "${archive}.part" && gzip -t "${archive}.part" \
            && mv "${archive}.part" "$archive"; }; then
        rm -f "${archive}.part"
        return 0
    fi

    # Trim in place: the same file, keeping its newest lines.
    local tail_tmp="${log_file}.tail.$$"
    tail -n "$keep_lines" "$log_file" > "$tail_tmp" && cat "$tail_tmp" > "$log_file"
    rm -f "$tail_tmp"

    grip_log "LOG-ROTATE" "INFO" "Rotated $(basename "$log_file"): ${size} bytes archived to ${archive}; kept the last ${keep_lines} lines"

    return 0
}

# launchd jobs write their stdout and stderr under logs/ (*.out.log and
# *.err.log), hold those files open, and never trim them. Measured 2026-11-24:
# logs/moat-anchor.out.log had reached 218 MB. The limit is
# GRIP_MAX_LAUNCHD_LOG_BYTES, 20 MB by default.
grip_rotate_launchd_logs() {
    local logs_dir="${CLAUDE_DIR:-$HOME/.claude}/logs"
    local log
    for log in "$logs_dir"/*.out.log "$logs_dir"/*.err.log; do
        grip_log_rotate "$log" "${GRIP_MAX_LAUNCHD_LOG_BYTES:-20971520}"
    done
}
