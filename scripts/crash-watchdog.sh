#!/usr/bin/env bash
# Crash Watchdog — auto-dismiss macOS crash dialogs and log SIGSEGV events.
#
# Runs every 30s via launchd. When macOS Problem Reporter appears:
# 1. Logs the crash to ~/.claude/logs/crash-watchdog.log
# 2. Dismisses the dialog via the HAL osa registry (preferred) or direct
#    osascript as a fallback.
#
# β-4b: migrated off direct osascript onto the HAL primitive so the
# detect+dismiss path goes through the registry's injection-safety
# guards. Direct osascript remains the fallback for hosts without HAL.
#
# Does NOT auto-reopen Claude Code — that would create unwanted sessions.
# The subprocess isolation in recall-hook.py prevents most crashes from
# reaching the parent process. This watchdog catches any that slip through.

CLAUDE_DIR="${HOME}/.claude"
LOG="${CLAUDE_DIR}/logs/crash-watchdog.log"
mkdir -p "$(dirname "$LOG")"

HAL_BIN="${HAL_HOME:-$HOME/.hal}/bin/hal"

detect_crash_dialog() {
    # Prefer HAL if reachable; its registry-backed dispatch already
    # returns the literal "none" when no dialog is present, so we pass
    # the stdout through verbatim.
    local result=""
    if [ -x "$HAL_BIN" ]; then
        result=$("$HAL_BIN" osa run crash_dialog_detect 2>/dev/null || echo "")
    fi
    if [ -n "$result" ]; then
        echo "$result"
        return
    fi
    # HAL unavailable or dispatch failed — fall back to direct osascript
    osascript -e '
tell application "System Events"
    set crashApps to {"Problem Reporter", "UserNotificationCenter"}
    repeat with appName in crashApps
        if exists process appName then
            try
                set winCount to count of windows of process appName
                if winCount > 0 then
                    return appName as text
                end if
            end try
        end if
    end repeat
    return "none"
end tell
' 2>/dev/null
}

dismiss_crash_dialog() {
    local name="$1"
    # HAL first — registry's process_name pattern whitelists the two
    # known crash-reporter apps; anything else is rejected before
    # reaching AppleScript.
    if [ -x "$HAL_BIN" ]; then
        if "$HAL_BIN" osa run crash_dialog_dismiss \
            --param "process_name=$name" >/dev/null 2>&1; then
            return 0
        fi
    fi
    # Fallback — direct osascript with the dialog name interpolated.
    # Safe here because $name originates from detect_crash_dialog which
    # either returned "none" (guarded above) or the literal process name
    # of Problem Reporter / UserNotificationCenter.
    osascript -e "
tell application \"System Events\"
    try
        tell process \"${name}\"
            -- Try OK button first (most common)
            if exists button \"OK\" of window 1 then
                click button \"OK\" of window 1
            -- Try Reopen
            else if exists button \"Reopen\" of window 1 then
                click button \"Reopen\" of window 1
            -- Try Close
            else if exists button \"Close\" of window 1 then
                click button \"Close\" of window 1
            -- Last resort: press Escape
            else
                key code 53
            end if
        end tell
    end try
end tell
" 2>/dev/null
}

CRASH_DIALOG=$(detect_crash_dialog)

if [[ "$CRASH_DIALOG" != "none" && -n "$CRASH_DIALOG" ]]; then
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "${TIMESTAMP} [WATCHDOG] Crash dialog detected: ${CRASH_DIALOG}" >> "$LOG"

    # Try to get crash details before dismissing — still direct
    # osascript because the window name is free text and not yet
    # part of the HAL registry.
    CRASH_INFO=$(osascript -e "
tell application \"System Events\"
    try
        set crashText to name of window 1 of process \"${CRASH_DIALOG}\"
        return crashText
    end try
    return \"unknown\"
end tell
" 2>/dev/null)
    echo "${TIMESTAMP} [WATCHDOG] Crash info: ${CRASH_INFO}" >> "$LOG"

    dismiss_crash_dialog "$CRASH_DIALOG"
    echo "${TIMESTAMP} [WATCHDOG] Dialog dismissed" >> "$LOG"
fi
