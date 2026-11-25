#!/usr/bin/env bash
# Midnight sync for PARAMOUNT_BACKUPS to private remote.
# Installed as a launchd agent: com.codetonight.paramount-backup.plist
set -euo pipefail

BACKUP_DIR="${HOME}/CodeTonight/PARAMOUNT_BACKUPS"
LOG="${HOME}/Library/Logs/paramount-backup.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

if [ ! -d "$BACKUP_DIR/.git" ]; then
    log "ERROR: $BACKUP_DIR is not a git repo"
    exit 1
fi

cd "$BACKUP_DIR"

# Pull first to prevent non-fast-forward divergence
git pull --rebase origin main 2>>"$LOG" || {
    log "WARNING: git pull --rebase failed, attempting push anyway"
}

git add -A
if git diff --cached --quiet; then
    log "Nothing to commit"
    exit 0
fi

git commit -m "backup: $(date +%Y-%m-%d)"
git push origin main 2>>"$LOG" || {
    log "ERROR: git push failed"
    exit 1
}
log "Backup pushed successfully"
