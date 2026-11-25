#!/usr/bin/env bash
# vps-tree-sync — periodic sync of the VPS ~/.claude to origin/main.
#
# The VPS checkout is a mirror that runs the relay, which self-deploys
# surgically every 3 min and can leave relay files staged at main's latest. A
# `git merge` can therefore never win (it refuses on those staged files), and
# a "skip when dirty" guard never syncs at all: measured 2026-10-02, the tree
# was 297 commits behind with every dirty path being main's own content vs a
# stale HEAD. So this brings the WHOLE tree to origin/main with `reset --hard`.
#
# Nothing is discarded silently. Before the reset, any tracked change (staged
# or unstaged) is written as a patch under logs/vps-tree-sync-discarded/ and
# its paths are logged, so a hand edit made on the VPS is recoverable with
# `git apply`. Untracked files are never touched by a reset and are left
# where they are. The one case a reset would lose for good, local COMMITS
# ahead of main, makes the script skip. Scheduled by cron on the VPS
# (0 * * * *).
set -euo pipefail

cd "$HOME/.claude"
LOG="$HOME/.claude/logs/vps-tree-sync.log"
RECEIPTS="$HOME/.claude/logs/vps-tree-sync-discarded"
mkdir -p "$(dirname "$LOG")"
ts() { date -u +%T; }

git fetch -q origin main || { echo "[$(ts)] fetch failed" >> "$LOG"; exit 0; }

if [ "$(git rev-list --count origin/main..HEAD)" -gt 0 ]; then
  echo "[$(ts)] skip: local commits ahead of main" >> "$LOG"
  exit 0
fi

# Receipt for anything the reset is about to discard: tracked paths and the
# full patch (staged and unstaged, vs HEAD), so a hand edit is recoverable.
dirty=$(git status --porcelain | grep -vE '^\?\?' || true)
if [ -n "$dirty" ]; then
  mkdir -p "$RECEIPTS"
  receipt="$RECEIPTS/$(date -u +%Y%m%dT%H%M%SZ).patch"
  git diff HEAD --binary > "$receipt" || true
  echo "[$(ts)] discarding tracked changes (receipt: $receipt):" >> "$LOG"
  printf '%s\n' "$dirty" >> "$LOG"
fi

before=$(git rev-parse --short HEAD)
git reset --hard origin/main >/dev/null
after=$(git rev-parse --short HEAD)
echo "[$(ts)] synced $before -> $after" >> "$LOG"
