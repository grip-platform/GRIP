#!/bin/bash
# sync-grip-plans.sh - Sync GRIP plans to grip backup
set -euo pipefail

SOURCE_DIR="$HOME/.claude/plans"
BACKUP_DIR="$HOME/grip/grip-plans"
LOG_FILE="$HOME/.claude/logs/plan-sync.log"

mkdir -p "$BACKUP_DIR" "$(dirname "$LOG_FILE")"

log() { echo "[$(date "+%Y-%m-%d %H:%M:%S")] $1" >> "$LOG_FILE"; }

if [[ -d "$SOURCE_DIR" ]] && [[ -n "$(ls -A "$SOURCE_DIR" 2>/dev/null)" ]]; then
    rsync -av --delete "$SOURCE_DIR/" "$BACKUP_DIR/" >> "$LOG_FILE" 2>&1
    log "Synced plans to grip"
else
    log "No plans to sync"
fi

tail -1000 "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv "$LOG_FILE.tmp" "$LOG_FILE" || true
