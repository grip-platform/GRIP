#!/usr/bin/env bash
# install-copilot-brief-launchd.sh — install the Copilot morning-brief launchd agent.
# macOS only. Idempotent. Safe to run multiple times.
set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
TEMPLATE="${CLAUDE_DIR}/launchd/com.grip.copilot-morning-brief.plist.template"
TARGET_DIR="${HOME}/Library/LaunchAgents"
TARGET="${TARGET_DIR}/com.grip.copilot-morning-brief.plist"
LABEL="com.grip.copilot-morning-brief"

if [ ! -f "${TEMPLATE}" ]; then
    echo "ERROR: template missing at ${TEMPLATE}" >&2
    exit 1
fi

mkdir -p "${TARGET_DIR}"
mkdir -p "${CLAUDE_DIR}/logs"

sed "s|__HOME__|${HOME}|g" "${TEMPLATE}" > "${TARGET}"
chmod 644 "${TARGET}"

if launchctl list | grep -q "${LABEL}"; then
    launchctl unload "${TARGET}" 2>/dev/null || true
fi
launchctl load -w "${TARGET}"

echo "Installed: ${TARGET}"
echo "Schedule: weekdays 7:00 SAST"
echo "Logs: ${CLAUDE_DIR}/logs/copilot-morning-brief.log"
echo
echo "To trigger now:    launchctl start ${LABEL}"
echo "To dry-run only:   PYTHONPATH=\${HOME}/.claude \${HOME}/.claude/venv/bin/python -m lib.copilot.brief morning --dry-run"
echo "To uninstall:      launchctl unload ${TARGET} && rm ${TARGET}"
