#!/usr/bin/env bash
# git-crypt smudge wrapper — auto-symlinks keys for worktrees.
#
# Problem: git-crypt stores keys at .git/git-crypt/keys/. In worktrees,
# the git dir is .git/worktrees/<name>/ which has no git-crypt/ subdir.
# The smudge filter fails during checkout: "Unable to open key file".
#
# Fix: detect worktree context, symlink keys from common dir, then
# pass through to real git-crypt smudge. Transparent to all consumers
# (Claude Desktop, grip-worktree.sh, manual worktree add).
#
# Usage (configured via git config):
#   filter.git-crypt.smudge = "$HOME/.claude/lib/git-crypt-smudge.sh" smudge
#   filter.git-crypt-codetonight.smudge = "$HOME/.claude/lib/git-crypt-smudge.sh" smudge --key-name=codetonight

set -euo pipefail

GIT_COMMON=$(git rev-parse --git-common-dir 2>/dev/null || true)
GIT_DIR=$(git rev-parse --git-dir 2>/dev/null || true)

# If common dir and git dir differ, we're in a worktree
if [ -n "$GIT_COMMON" ] && [ -n "$GIT_DIR" ] && [ "$GIT_COMMON" != "$GIT_DIR" ]; then
  # Resolve to absolute paths for reliable comparison
  GIT_COMMON=$(cd "$GIT_COMMON" && pwd)
  GIT_DIR=$(cd "$GIT_DIR" && pwd)

  if [ "$GIT_COMMON" != "$GIT_DIR" ]; then
    # Symlink git-crypt keys if not already present
    if [ -d "$GIT_COMMON/git-crypt" ] && [ ! -e "$GIT_DIR/git-crypt" ]; then
      ln -s "$GIT_COMMON/git-crypt" "$GIT_DIR/git-crypt" 2>/dev/null || true
    fi
  fi
fi

# Pass through to real git-crypt smudge with all original arguments
exec "git-crypt" "$@"
