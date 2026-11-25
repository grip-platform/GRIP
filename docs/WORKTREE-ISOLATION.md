# Git Worktree Session Isolation (REMOVED)

Removed in RSI KISS Convergence (Feb 2026).

## Why

Claude Code 2.1.49+ handles worktree isolation natively:

- `isolation: "worktree"` in Task agent definitions
- `--worktree` (`-w`) CLI flag
- WorktreeCreate/WorktreeRemove hooks for lifecycle

## What was deleted

| File | Lines | Purpose |
|------|-------|---------|
| `lib/worktree-manager.sh` | 246 | Create/destroy/list worktrees |
| `lib/worktree_registry.py` | 111 | JSON registry of active worktrees |
| `tests/test_worktree_registry.py` | 87 | Registry unit tests |

## What remains

- `lib/concurrent_session_detector.py` -- still powers anti-drift detection (independent of worktrees)
- `git worktree prune` in startup hook -- cleans stale admin data

## Rollback

`git revert <commit>` restores the files. The `if [[ -f "$LIB_DIR/worktree-manager.sh" ]]` guards in the hooks meant the feature was already optional.

## References

- GRIP Issue #92
- Claude Code 2.1.49 release notes
