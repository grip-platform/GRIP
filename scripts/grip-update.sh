#!/usr/bin/env bash
# grip-update.sh — Pull latest GRIP to ~/.claude
#
# Usage:
#   bash ~/.claude/scripts/grip-update.sh
#   grip-update  (if aliased)
#
# Cross-platform: macOS (zsh/bash), Linux (bash), Windows (Git Bash)

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

if [ ! -d "$CLAUDE_DIR/.git" ]; then
  echo "[ERROR] $CLAUDE_DIR is not a git repository. Run grip-repair.sh first."
  exit 1
fi

cd "$CLAUDE_DIR"

# Auto-stash if dirty
stashed=false
if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
  echo "[INFO] Stashing local changes..."
  git stash push -m "grip-update auto-stash $(date +%Y%m%d-%H%M%S)" && stashed=true
fi

# Pull latest
if git pull --rebase origin main; then
  echo "[OK] GRIP updated to $(git rev-parse --short HEAD)"
else
  echo "[WARN] Pull failed — check network or resolve conflicts"
  if $stashed; then
    git stash pop 2>/dev/null || true
  fi
  exit 1
fi

# Restore stash
if $stashed; then
  if git stash pop; then
    echo "[INFO] Restored local changes"
  else
    echo "[WARN] Stash pop had conflicts — resolve manually"
  fi
fi

# Fix permissions (quick — no venv rebuild)
for f in "$CLAUDE_DIR"/hooks/*.sh "$CLAUDE_DIR"/scripts/*.sh "$CLAUDE_DIR"/bin/*; do
  [ -f "$f" ] && chmod +x "$f"
done

echo "[OK] GRIP is up to date."
