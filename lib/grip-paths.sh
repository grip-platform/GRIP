#!/usr/bin/env bash
#
# grip-paths.sh — Cross-platform GRIP path helpers
#
# Source this file once in each shell entry point:
#   source "${LIB_DIR:-$HOME/.claude/lib}/grip-paths.sh"
#
# After sourcing, GRIP_TMP_DIR is exported and all grip_*_path()
# functions are available. Never re-derive the temp dir inline.

# ---------------------------------------------------------------------------
# GRIP_TMP_DIR
#   Honours $TMPDIR (set by launchd/macOS), $TEMP/$TMP (Windows), /tmp fallback.
#   Exported so child processes inherit it.
# ---------------------------------------------------------------------------
if [[ -z "${GRIP_TMP_DIR:-}" ]]; then
    GRIP_TMP_DIR="${TMPDIR:-${TEMP:-${TMP:-/tmp}}}"
    export GRIP_TMP_DIR
fi

# ---------------------------------------------------------------------------
# Path helpers — mirror lib/_compat.py so shell and Python agree
# ---------------------------------------------------------------------------

# Socket path uses /tmp on macOS to avoid AF_UNIX 104-byte sun_path limit.
# tempfile.gettempdir() returns /var/folders/.../ (48 chars) on macOS,
# which combined with UUID session IDs hits the limit.
grip_socket_path() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "${TMPDIR:-/tmp}/grip-session-${1}.sock"
    else
        echo "${GRIP_TMP_DIR}/grip-session-${1}.sock"
    fi
}
grip_instance_path()  { echo "${GRIP_TMP_DIR}/grip-instance-${1}.id"; }
grip_sessions_dir()   { echo "${GRIP_TMP_DIR}/grip-sessions"; }
grip_injection_path() { echo "${GRIP_TMP_DIR}/grip-injected-${1}.txt"; }
grip_server_pid_path(){ echo "${GRIP_TMP_DIR}/grip-session-server-${1}.pid"; }
grip_broadcast_path() { echo "${GRIP_TMP_DIR}/grip-broadcast-${1}.session"; }
grip_session_map_path(){ echo "${GRIP_TMP_DIR}/grip-session-map-${1}"; }
grip_tmp_file()       { echo "${GRIP_TMP_DIR}/${1}"; }

# ---------------------------------------------------------------------------
# Stale per-session mode files (GRIP#8743)
#   grip_sweep_stale_mode_files deletes grip-session-mode(s)-<pid> files whose
#   Claude Code process is gone. On Windows those files carry Claude Code's
#   native PID, which Git Bash's kill -0 cannot see, so `ps -W` decides: a PID
#   is alive when it is field 1 (PID) or field 4 (WINPID) of a row. Cygwin's ps
#   can start a row with a status letter, which moves them to fields 2 and 5.
#   When that list cannot be read, or its header does not put PID first and
#   WINPID fourth, nothing is deleted.
# ---------------------------------------------------------------------------
grip_sweep_stale_mode_files() {
    local listing="" f pid
    if [[ "$(uname -s)" == MINGW* ]] || [[ "$(uname -s)" == MSYS* ]]; then
        listing=$(ps -W 2>/dev/null) || return 0
        [[ -n "$listing" ]] || return 0
        printf '%s\n' "$listing" \
            | awk 'NR == 1 { ok = ($1 == "PID" && $4 == "WINPID") } END { exit !ok }' || return 0
    fi
    for f in "${GRIP_TMP_DIR}"/grip-session-mode-* "${GRIP_TMP_DIR}"/grip-session-modes-*; do
        [[ -f "$f" ]] || continue
        pid=$(basename "$f" | sed 's/grip-session-modes\{0,1\}-//')
        [[ -n "$pid" ]] || continue
        if [[ -n "$listing" ]]; then
            printf '%s\n' "$listing" | awk -v p="$pid" \
                '{ o = ($1 ~ /^[A-Za-z]$/) } $(1 + o) == p || $(4 + o) == p { found = 1 } END { exit !found }' \
                && continue
        elif kill -0 "$pid" 2>/dev/null; then
            continue
        fi
        rm -f "$f"
    done
}

# ---------------------------------------------------------------------------
# Claude Code's own PID on Windows (GRIP#8817)
#   grip_windows_cc_pid prints GRIP_CC_PID, which init.sh keeps, else CLAUDE_PID,
#   which Claude Code gives every process it starts. Digits only, and never 1,
#   the $PPID that a native parent leaves behind. It prints nothing otherwise.
# ---------------------------------------------------------------------------
grip_windows_cc_pid() {
    local pid="${GRIP_CC_PID:-${CLAUDE_PID:-}}"
    if [[ "$pid" =~ ^[0-9]+$ && "$pid" != "1" ]]; then
        printf '%s\n' "$pid"
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Logs directory + wave-position resolution — mirror lib/state_paths.py so the
# shell and Python surfaces cannot disagree.
# ---------------------------------------------------------------------------

# Ensure the GRIP logs directory exists (race-safe, install-safe, idempotent).
# Intentionally does NOT create $CLAUDE_DIR itself: scripts/install.sh sources
# helpers before cloning into ~/.claude, and a logs/ dir made there would make a
# fresh install refuse ("~/.claude already exists"; GRIP#8470).
grip_ensure_log_dir() {
    local dir="${CLAUDE_DIR:-$HOME/.claude}/logs"
    [[ -d "$dir" ]] && return 0
    [[ -d "${dir%/*}" ]] && mkdir -p "$dir" 2>/dev/null
    return 0
}

# Resolve the active wave-position file, preferring the migrated state path.
# lib/wave_state.py writes state/session/wave-position; the root .wave-position
# is the pre-migration name, read only while it still exists (GRIP#8470).
# Mirrors wave_position_path() in lib/state_paths.py.
grip_wave_position_path() {
    local claude_dir="${1:-${CLAUDE_DIR:-$HOME/.claude}}"
    if [[ -f "$claude_dir/state/session/wave-position" ]]; then
        echo "$claude_dir/state/session/wave-position"
    else
        echo "$claude_dir/.wave-position"
    fi
}
