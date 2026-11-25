#!/usr/bin/env bash
# Install (or refresh) the com.grip.channel LaunchAgent so the grip-channel
# WhatsApp/Web daemon auto-starts at login and auto-restarts on crash.
#
# Idempotent + per-machine: generates ~/Library/LaunchAgents/com.grip.channel.plist
# with this machine's absolute paths, validates it, then boots it via the modern
# launchctl bootstrap/kickstart API. Safe to re-run.
#
# The daemon target is scripts/run-daemon.sh (rebuild-if-stale -> load-env ->
# exec node dist/index.js). run-daemon.sh already frees ports 3100/3101 on start,
# so launchd taking over from a manually-started instance is a clean handoff.
#
# macOS only (launchd). On Linux the VPS runs grip-channel under pm2 instead.
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "[install-grip-channel-launchd] macOS only (Linux/VPS uses pm2). Skipping." >&2
  exit 0
fi

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
DAEMON_DIR="$CLAUDE_DIR/mcp-servers/grip-channel"
RUN_DAEMON="$DAEMON_DIR/scripts/run-daemon.sh"
LABEL="com.grip.channel"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$CLAUDE_DIR/logs"
NODE_BIN_DIR="$(cd "$(dirname "$(command -v node)")" && pwd)"   # e.g. /opt/homebrew/bin
GUI="gui/$(id -u)"

[ -f "$RUN_DAEMON" ] || { echo "[install-grip-channel-launchd] FATAL: $RUN_DAEMON not found" >&2; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- Generated per-machine by scripts/install-grip-channel-launchd.sh. Do not hand-edit;
     re-run the installer to regenerate. -->
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${RUN_DAEMON}</string>
  </array>
  <key>WorkingDirectory</key><string>${DAEMON_DIR}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>${HOME}</string>
    <key>PATH</key><string>${NODE_BIN_DIR}:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>CLAUDE_DIR</key><string>${CLAUDE_DIR}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>${LOG_DIR}/grip-channel.out.log</string>
  <key>StandardErrorPath</key><string>${LOG_DIR}/grip-channel.err.log</string>
</dict>
</plist>
PLIST_EOF

# Validate before loading — a malformed plist silently no-ops otherwise.
plutil -lint "$PLIST" >/dev/null

# Idempotent (re)load: bootout an existing instance, then bootstrap fresh.
launchctl bootout "$GUI/$LABEL" 2>/dev/null || true
launchctl bootstrap "$GUI" "$PLIST"
launchctl kickstart -k "$GUI/$LABEL" 2>/dev/null || true

echo "[install-grip-channel-launchd] installed + loaded: $PLIST"
echo "[install-grip-channel-launchd] status:"
launchctl print "$GUI/$LABEL" 2>/dev/null | grep -E '^\s*(state|pid|program|last exit) ' || \
  launchctl list | grep "$LABEL" || true
