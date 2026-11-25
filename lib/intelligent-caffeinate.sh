#!/usr/bin/env bash
#
# Intelligent Caffeinate - Prevents macOS sleep during autonomous RSI loops
#
# Features:
#   - Only activates when autonomous mode is detected
#   - Ties to parent Claude Code process (auto-dies with session)
#   - Max duration safety limit (default 8h)
#   - PID file for clean shutdown
#   - Idempotent: safe to call multiple times
#   - Keeps the Mac awake and lets the screen sleep (see CAFFEINATE_FLAGS)
#
# Usage:
#   source lib/intelligent-caffeinate.sh
#   caffeinate_start        # Start if autonomous mode is on
#   caffeinate_stop         # Stop gracefully
#   caffeinate_status       # Check if running
#
# Issue: https://github.com/grip-platform/GRIP/issues/59
#

[[ -z "${CLAUDE_DIR:-}" ]] && CLAUDE_DIR="$HOME/.claude"
[[ -z "${LIB_DIR:-}" ]] && LIB_DIR="$CLAUDE_DIR/lib"
[[ -z "${HOOK_LOG:-}" ]] && HOOK_LOG="$CLAUDE_DIR/logs/hooks.log"
if [[ -f "$LIB_DIR/grip-paths.sh" ]]; then
    # shellcheck source=/dev/null
    source "$LIB_DIR/grip-paths.sh"
fi
grip_ensure_log_dir

CAFFEINATE_PID_FILE="${TMPDIR:-/tmp}/grip-caffeinate.pid"
CAFFEINATE_MAX_HOURS="${GRIP_CAFFEINATE_MAX_HOURS:-8}"
RSI_LOCK_FILE="${CLAUDE_DIR}/state/rsi/rsi-active.lock"

# GRIP's keep-awake keeps the Mac awake and lets the screen sleep on its own timer:
#   -i  prevent idle system sleep
#   -m  prevent idle disk sleep
#   -s  prevent system sleep (on mains power only)
# It leaves out -d (prevent display sleep) and -u (declare the user active) on
# purpose. Under -t, each one holds the screen on for the whole run, and the old
# -dimsu kept the screen lit for 8 hours at a time (V>> 2026-11-07).
CAFFEINATE_FLAGS="-ims"
# GRIP knows its own keep-awake only by the PID file. A caffeinate that GRIP did
# not record is never adopted or stopped, whatever its flags, because flags are
# not proof of ownership: anyone can type `caffeinate -ims -t 3600`. A keep-awake
# of GRIP's whose PID file was lost ends by itself at its -t limit.
# GRIP's flags before 2026-11-07, which hold the screen on until the run expires.
# The keep-awake in the PID file is retired and replaced if it still has them.
_CAFF_LEGACY_SIGNATURE="caffeinate -dimsu"
# A record names a process that began before the record was written. A process
# at the recorded PID that began more than this many seconds after the PID file
# was written has reused the PID, so it is not GRIP's (GRIP#8991). The check
# compares two readings taken a moment apart, each in whole seconds, so it needs
# a few seconds. A minute is deliberately wider, because too small a margin makes
# GRIP's own keep-awake look like a stranger. commands/caffeinate.md says why.
_CAFF_REUSE_SLACK_S=60

_caff_log() {
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    echo "[$ts] [CAFFEINATE] $*" >> "$HOOK_LOG" 2>/dev/null || true
}

# Check if autonomous mode is active
_is_autonomous() {
    # Check env var first (fastest)
    if [[ "${GRIP_AUTONOMOUS:-0}" == "1" ]]; then
        return 0
    fi

    # Check autonomous state file
    local state_file="$CLAUDE_DIR/.autonomous-state.json"
    if [[ -f "$state_file" ]]; then
        local level
        level=$(python3 -c "
import json, pathlib
s = json.loads(pathlib.Path('$state_file').read_text())
print(s.get('level', 0))
" 2>/dev/null) || level="0"
        [[ "$level" -gt 0 ]] && return 0
    fi

    # Check the RSI lock (caffeinate should stay active during RSI loops)
    if _caff_lock_owner_alive; then
        return 0
    fi

    return 1
}

# True while the RSI lock names a live process, the rule lib/rsi/lock.py applies:
# a lock with no live owner is stale. A lock with no pid, written by a test of
# lib/session_control_server.py, once made every session look like a running
# loop and kept the Mac awake for 8 hours after each start (GRIP#8989).
# A lock that cannot be read is read once more after a short pause, because its
# writers empty the file before writing it. One that still cannot be read is
# stale, as lib/rsi/lock.py treats it.
_caff_lock_owner_alive() {
    [[ -f "$RSI_LOCK_FILE" ]] || return 1
    local pid
    pid=$(python3 -c 'import json, sys, time
for attempt in range(2):
    try:
        print(int(json.load(open(sys.argv[1])).get("pid") or 0))
        break
    except Exception:
        time.sleep(0.3)
else:
    print(0)' "$RSI_LOCK_FILE" 2>/dev/null) || return 1
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$pid" 2>/dev/null
}

# Check if caffeinate is already running for this GRIP session
_is_caffeinate_running() {
    if [[ -f "$CAFFEINATE_PID_FILE" ]]; then
        local pid
        pid=$(< "$CAFFEINATE_PID_FILE") || return 1
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            local kind=0
            _caff_is_ours "$pid" || kind=$?
            # 0: GRIP's keep-awake. 2: ps could not say, so the record is kept.
            [[ "$kind" -ne 1 ]] && return 0
        fi
        # Stale PID file, or a PID that another process now uses
        rm -f "$CAFFEINATE_PID_FILE"
    fi
    return 1
}

# The PID in the PID file. Fails when there is no record.
_caff_recorded_pid() {
    [[ -f "$CAFFEINATE_PID_FILE" ]] || return 1
    local pid
    pid=$(< "$CAFFEINATE_PID_FILE") || return 1
    [[ -n "$pid" ]] || return 1
    printf '%s\n' "$pid"
}

# Is the process at the recorded PID still GRIP's keep-awake? Returns 0 when
# ps shows a caffeinate that began before the PID file was written, 1 when ps
# shows another program or a process that began later (the PID was reused),
# and 2 when ps cannot say. Callers drop the record only on 1, and signal the
# process only on 0, so GRIP never stops a process it cannot place (GRIP#8991).
_caff_is_ours() {
    local kind=0 began=0
    _caff_is_caffeinate "$1" || kind=$?
    _caff_began_before_record "$1" || began=$?
    [[ "$kind" -eq 1 || "$began" -eq 1 ]] && return 1
    [[ "$kind" -eq 0 && "$began" -eq 0 ]] && return 0
    return 2
}

# Is the process a caffeinate, after an optional path? Returns 0 when it is,
# 1 when it is another program, and 2 when ps cannot say. A zombie shows in
# ps as <defunct>, so it counts as another program.
_caff_is_caffeinate() {
    local cmd caff_re
    cmd=$(ps -o command= -p "$1" 2>/dev/null) || return 2
    [[ -n "$cmd" ]] || return 2
    caff_re='^[[:space:]]*([^[:space:]]*/)?caffeinate([[:space:]]|$)'
    [[ "$cmd" =~ $caff_re ]] && return 0
    return 1
}

# Did the process at a PID begin before the PID file was written, within
# _CAFF_REUSE_SLACK_S? Returns 0 when it did, 1 when it began later, and 2
# when ps or the file's date cannot say.
_caff_began_before_record() {
    local age written now
    age=$(_caff_etime_seconds "$(ps -o etime= -p "$1" 2>/dev/null)") || return 2
    written=$(_caff_mtime "$CAFFEINATE_PID_FILE") || return 2
    now=$(date +%s)
    (( now - written <= age + _CAFF_REUSE_SLACK_S )) && return 0
    return 1
}

# Seconds in a ps etime value, [[dd-]hh:]mm:ss, or bare seconds as some BSD
# builds print them. Fails on any other text, which then counts as "cannot say".
_caff_etime_seconds() {
    local e="${1//[[:space:]]/}" days=0 hours=0 minutes=0 rest etime_re
    etime_re='^(([0-9]+-)?([0-9]+:)?[0-9]+:)?[0-9]+$'
    [[ "$e" =~ $etime_re ]] || return 1
    if [[ "$e" == *-* ]]; then
        days="${e%%-*}"
        e="${e#*-}"
    fi
    if [[ "$e" == *:* ]]; then
        rest="${e%:*}"
        minutes="${rest##*:}"
        [[ "$rest" == *:* ]] && hours="${rest%%:*}"
    fi
    echo $(( ((10#$days * 24 + 10#$hours) * 60 + 10#$minutes) * 60 + 10#${e##*:} ))
}

# A file's modification time in seconds since the epoch, from GNU or BSD stat.
_caff_mtime() {
    local t digits_re='^[0-9]+$'
    t=$(stat -c %Y "$1" 2>/dev/null) || t=$(stat -f %m "$1" 2>/dev/null) || return 1
    [[ "$t" =~ $digits_re ]] || return 1
    printf '%s\n' "$t"
}

# True when ps shows the process at a recorded PID is GRIP's keep-awake still
# running with the old flags, which hold the screen on. The command must start
# with them, after an optional path. A caffeinate someone else started with
# the same flags, for example to keep the screen on for a presentation, is
# not GRIP's, and neither is one that reused the PID. Nothing is stopped here.
_caff_pid_is_legacy() {
    local cmd legacy_re
    _caff_is_ours "$1" || return 1
    cmd=$(ps -o command= -p "$1" 2>/dev/null) || return 1
    legacy_re='^[[:space:]]*([^[:space:]]*/)?'"$_CAFF_LEGACY_SIGNATURE"'([[:space:]]|$)'
    [[ "$cmd" =~ $legacy_re ]]
}

# True when the keep-awake in the PID file still runs with GRIP's old flags.
_caff_recorded_is_legacy() {
    local pid
    pid=$(_caff_recorded_pid) || return 1
    _caff_pid_is_legacy "$pid"
}

# Stop the recorded keep-awake if it still has GRIP's old flags. The PID is
# read once, so the process judged is the process signalled. Returns 0 only
# when it stopped one, and the caller then starts the replacement, so the Mac
# stays awake throughout.
_caff_retire_legacy() {
    local pid
    pid=$(_caff_recorded_pid) || return 1
    _caff_pid_is_legacy "$pid" || return 1
    if ! kill "$pid" 2>/dev/null; then
        _caff_log "Could not stop a keep-awake that holds the screen on (PID: $pid)"
        return 1
    fi
    _caff_log "Retired a keep-awake that held the screen on (PID: $pid)"
    rm -f "$CAFFEINATE_PID_FILE"
    return 0
}

# Start caffeinate if autonomous mode is active
# Write the record for the keep-awake just started, or end it and say why
# (GRIP#9354). GRIP knows its keep-awake only by this record, so one it could not
# record could be neither seen nor stopped, and would hold the Mac awake until its
# own -t limit. Returns 1 when it ended the process; the callers then return 0,
# so a `set -e` caller is not ended by a record it could not write.
_caff_record_or_end() {
    local caff_pid="$1" label="$2"
    if { echo "$caff_pid" > "$CAFFEINATE_PID_FILE"; } 2>/dev/null; then
        _caff_log "${label}Started (PID: $caff_pid, max: ${CAFFEINATE_MAX_HOURS}h)"
        return 0
    fi
    kill "$caff_pid" 2>/dev/null || true
    _caff_log "${label}Not started: its record $CAFFEINATE_PID_FILE could not be written"
    return 1
}

caffeinate_start() {
    # Only on macOS
    if [[ "$(uname)" != "Darwin" ]]; then
        _caff_log "Not macOS, skipping"
        return 0
    fi

    # Check if caffeinate binary exists
    if ! command -v caffeinate &>/dev/null; then
        _caff_log "caffeinate not found"
        return 0
    fi

    # Replace a recorded keep-awake that still holds the screen on. This runs
    # outside a loop too, because whoever started it may still need the Mac
    # awake: the replacement keeps it awake without the screen.
    local replaced=false
    _caff_retire_legacy && replaced=true

    # Skip if not autonomous, unless a replacement is due
    if [[ "$replaced" == false ]] && ! _is_autonomous; then
        _caff_log "Not autonomous mode, skipping"
        return 0
    fi

    # Skip if already running (PID file)
    if _is_caffeinate_running; then
        _caff_log "Already running (PID: $(< "$CAFFEINATE_PID_FILE"))"
        return 0
    fi

    # Calculate max duration in seconds
    local max_seconds=$(( CAFFEINATE_MAX_HOURS * 3600 ))

    # Start caffeinate with GRIP's flags (CAFFEINATE_FLAGS) and a duration limit (-t)
    caffeinate "$CAFFEINATE_FLAGS" -t "$max_seconds" &
    local caff_pid=$!

    _caff_record_or_end "$caff_pid" "" || return 0

    # Log to terminal if available
    ( echo "  caffeinate: active (${CAFFEINATE_MAX_HOURS}h max)" > /dev/tty ) 2>/dev/null || true

    return 0
}

# Stop the keep-awake named in the PID file, and nothing else. It is signalled
# only when ps confirms it is GRIP's (_caff_is_ours). When ps cannot say, it is
# left running with its record, and it ends at its own -t limit. Returns 0 in
# every case, because the session-end hook calls this under set -e.
caffeinate_stop() {
    if _is_caffeinate_running; then
        local pid kind=0
        pid=$(< "$CAFFEINATE_PID_FILE")
        _caff_is_ours "$pid" || kind=$?
        if [[ "$kind" -ne 0 ]]; then
            _caff_log "Left running: ps could not confirm PID $pid is GRIP's keep-awake"
            return 0
        fi
        if kill "$pid" 2>/dev/null; then
            _caff_log "Stopped (PID: $pid)"
        else
            _caff_log "PID $pid already exited"
        fi
    fi
    rm -f "$CAFFEINATE_PID_FILE"
    return 0
}

# Start caffeinate unconditionally for RSI (bypasses autonomous check)
caffeinate_start_for_rsi() {
    # Only on macOS
    if [[ "$(uname)" != "Darwin" ]]; then
        return 0
    fi
    if ! command -v caffeinate &>/dev/null; then
        return 0
    fi
    # Replace a recorded keep-awake that still holds the screen on. Its status
    # is 1 when there is nothing to replace, so consume it: a caller running
    # with `set -e` must still reach the start below.
    _caff_retire_legacy || true
    # Idempotent: skip if already running
    if _is_caffeinate_running; then
        _caff_log "RSI: Already running (PID: $(< "$CAFFEINATE_PID_FILE"))"
        return 0
    fi
    local max_seconds=$(( CAFFEINATE_MAX_HOURS * 3600 ))
    caffeinate "$CAFFEINATE_FLAGS" -t "$max_seconds" &
    local caff_pid=$!
    _caff_record_or_end "$caff_pid" "RSI: " || return 0
    return 0
}

# Stop caffeinate only if RSI lock is NOT held
caffeinate_stop_if_safe() {
    # If a live RSI loop holds the lock, keep caffeinate running
    if _caff_lock_owner_alive; then
        _caff_log "RSI lock active, keeping caffeinate alive"
        return 0
    fi
    caffeinate_stop
}

# Get caffeinate status
caffeinate_status() {
    if _is_caffeinate_running; then
        local pid
        pid=$(< "$CAFFEINATE_PID_FILE")
        echo "active (PID: $pid)"
    else
        echo "inactive"
    fi
}
