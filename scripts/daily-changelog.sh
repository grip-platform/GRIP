#!/bin/bash
set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
LOG_DIR="$CLAUDE_DIR/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/daily-changelog.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

log "Starting daily changelog generation"

COMMIT_COUNT=$(git -C "$CLAUDE_DIR" log --since="24 hours ago" --oneline --no-merges | wc -l | tr -d ' ')

if [ "$COMMIT_COUNT" -eq 0 ]; then
    log "No commits in last 24 hours. Skipping."
    exit 0
fi

log "Found $COMMIT_COUNT commits. Invoking broly..."

claude -p \
    "/broly fork Analyze the last 24 hours of git commits in ~/.claude and prepend a dated changelog entry to ~/.claude/CHANGELOG.md. Use the existing format: date header, categorized sections (MCP, Modes, Infrastructure, etc), condensed bullet points. Do NOT rewrite existing entries." \
    --no-session-persistence \
    --max-budget-usd 5.00 \
    >> "$LOG_FILE" 2>&1

log "Daily changelog generation completed"
