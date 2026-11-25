#!/usr/bin/env bash
# install-osa-launchd.sh — install GRIP OSA macro launchd agents
#
# Installs two LaunchAgents:
#   com.grip.morning-macro  — fires /mac morning at 08:00
#   com.grip.wrap-macro     — fires /mac wrap   at 19:00
#
# Usage:
#   bash ~/.claude/scripts/install-osa-launchd.sh          # install
#   bash ~/.claude/scripts/install-osa-launchd.sh unload   # unload (keep plists)
#   bash ~/.claude/scripts/install-osa-launchd.sh remove   # unload + delete plists

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
LAUNCHD_DIR="$CLAUDE_DIR/launchd"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LABELS=(
    "com.grip.morning-macro"
    "com.grip.wrap-macro"
)

install_plists() {
    mkdir -p "$AGENTS_DIR"
    for label in "${LABELS[@]}"; do
        template="$LAUNCHD_DIR/${label}.plist.template"
        dest="$AGENTS_DIR/${label}.plist"
        if [[ ! -f "$template" ]]; then
            echo "ERROR: template not found: $template" >&2
            exit 1
        fi
        sed "s|__HOME__|$HOME|g" "$template" > "$dest"
        echo "  written: $dest"
    done
}

load_agents() {
    for label in "${LABELS[@]}"; do
        plist="$AGENTS_DIR/${label}.plist"
        # Unload first in case already loaded (idempotent)
        launchctl unload "$plist" 2>/dev/null || true
        launchctl load "$plist"
        echo "  loaded:  $label"
    done
}

unload_agents() {
    for label in "${LABELS[@]}"; do
        plist="$AGENTS_DIR/${label}.plist"
        if [[ -f "$plist" ]]; then
            launchctl unload "$plist" 2>/dev/null || true
            echo "  unloaded: $label"
        fi
    done
}

remove_plists() {
    for label in "${LABELS[@]}"; do
        plist="$AGENTS_DIR/${label}.plist"
        if [[ -f "$plist" ]]; then
            rm "$plist"
            echo "  removed: $plist"
        fi
    done
}

smoke_test() {
    echo ""
    echo "Smoke test — verifying launchctl list sees the agents:"
    for label in "${LABELS[@]}"; do
        if launchctl list "$label" 2>/dev/null | grep -q "Label"; then
            echo "  [PASS] $label"
        else
            echo "  [FAIL] $label — not listed by launchctl"
        fi
    done
}

cmd="${1:-install}"
case "$cmd" in
    install)
        echo "Installing GRIP OSA launchd agents..."
        install_plists
        load_agents
        smoke_test
        echo ""
        echo "Done. Schedules:"
        echo "  morning macro: 08:00 daily  → ~/.claude/logs/launchd-morning.log"
        echo "  wrap macro:    19:00 daily  → ~/.claude/logs/launchd-wrap.log"
        ;;
    unload)
        echo "Unloading GRIP OSA launchd agents..."
        unload_agents
        echo "Done. Plist files kept in $AGENTS_DIR"
        ;;
    remove)
        echo "Removing GRIP OSA launchd agents..."
        unload_agents
        remove_plists
        echo "Done."
        ;;
    *)
        echo "Usage: $0 [install|unload|remove]" >&2
        exit 1
        ;;
esac
