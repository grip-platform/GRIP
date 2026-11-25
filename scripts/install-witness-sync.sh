#!/usr/bin/env bash
# install-witness-sync.sh — install (or uninstall) the self-perpetuating public
# RSI-witness launchd agent on macOS. Substitutes __GRIP_HOME__ into the plist
# template and (re)loads the agent so it ticks every 20 min via
# bin/grip-rsi-witness-sync.
#
# Usage:
#   scripts/install-witness-sync.sh          # install + load
#   scripts/install-witness-sync.sh --uninstall
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GRIP_ROOT="$(dirname "$SCRIPT_DIR")"
LABEL="com.grip.rsi-witness-sync"
AGENTS_DIR="$HOME/Library/LaunchAgents"
TARGET="$AGENTS_DIR/$LABEL.plist"

_bootout() {
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$TARGET" 2>/dev/null || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
    _bootout
    rm -f "$TARGET"
    echo "uninstalled $LABEL"
    exit 0
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "launchd is macOS-only; on Linux use a cron entry calling $GRIP_ROOT/bin/grip-rsi-witness-sync" >&2
    exit 1
fi

mkdir -p "$AGENTS_DIR"

# The schedule lives in the template, NOT in a heredoc here. This script used to
# carry its own copy writing `StartInterval 1200`, which meant the repo held two
# disagreeing definitions of the same agent: running this would silently revert the
# calendar trigger that PR #4299 installed after measuring the interval never firing.
# One definition, one place, and config/launchd is the place the watchdog already
# uses. tests/test_rsi_witness_sync_plist.py asserts the schedule; this script only
# substitutes the path and loads it.
TEMPLATE="$GRIP_ROOT/config/launchd/$LABEL.plist.template"
if [[ ! -f "$TEMPLATE" ]]; then
    echo "missing template: $TEMPLATE" >&2
    exit 1
fi
# Substitution without sed. `sed "s|__GRIP_HOME__|$GRIP_ROOT|g"` embeds the path inside
# the expression, so a GRIP_ROOT containing the delimiter `|` — legal in a POSIX
# filename — breaks the expression or mis-substitutes. Bash parameter expansion treats
# the replacement as a literal string, so no character in the path can corrupt it.
{
    while IFS= read -r line || [[ -n "$line" ]]; do
        printf '%s\n' "${line//__GRIP_HOME__/$GRIP_ROOT}"
    done
} < "$TEMPLATE" > "$TARGET"

# Fail loudly rather than loading a broken agent — a plist that does not parse
# installs an agent that never runs, which is the silent-failure class this whole
# subsystem exists to catch.
#
# Validation is unconditional. It was previously wrapped in `command -v plutil`, which
# meant a missing plutil silently SKIPPED the check rather than failing it — turning the
# one guard against malformed XML into a no-op exactly when it could not be relied on.
# This script already exits above unless it is running on Darwin, where plutil is a
# system binary, so its absence means something is wrong and is worth stopping for.
if ! command -v plutil >/dev/null 2>&1; then
    echo "plutil not found on a Darwin system — cannot validate the generated plist" >&2
    exit 1
fi
plutil -lint "$TARGET" >/dev/null || { echo "generated plist failed plutil -lint" >&2; exit 1; }
if grep -q '__GRIP_HOME__' "$TARGET"; then
    echo "substitution failed: __GRIP_HOME__ still present in $TARGET" >&2
    exit 1
fi

# The plist can be perfectly valid and still point at nothing. launchd accepts such an
# agent, reports no error, and simply never produces output — indistinguishable from a
# job that is merely idle, which is how this subsystem lost ~10 days in July. Check the
# publisher is actually there and runnable before handing the plist to launchd.
PROGRAM="$GRIP_ROOT/bin/grip-rsi-witness-sync"
if [[ ! -x "$PROGRAM" ]]; then
    echo "publisher missing or not executable: $PROGRAM" >&2
    echo "the plist would install cleanly and never run" >&2
    exit 1
fi

_bootout
# bootstrap is the modern loader; fall back to load on older macOS.
launchctl bootstrap "gui/$(id -u)" "$TARGET" 2>/dev/null || launchctl load "$TARGET"
echo "installed + loaded $LABEL (ticks every 20 min -> $GRIP_ROOT/bin/grip-rsi-witness-sync)"
echo "log: $GRIP_ROOT/logs/rsi-witness-sync.log"
