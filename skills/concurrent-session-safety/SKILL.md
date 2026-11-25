---
name: concurrent-session-safety
description: Prevent data loss and drift when several Claude Code sessions share one ~/.claude directory on different branches. Covers stash cross-contamination, HEAD churn, and worktree discipline. Use when two or more sessions run at once.
---

# Concurrent Session Safety

Prevent data loss and drift when multiple Claude Code sessions work on different branches simultaneously.

## Problem

Multiple concurrent sessions sharing the same `~/.claude` directory cause three failure modes:

1. **Stash cross-contamination**: `git stash pop` on a different branch base silently applies changes to wrong context
2. **JSONL write corruption**: Multiple sessions appending to same JSONL files without locks can interleave or corrupt lines
3. **Working tree drift**: Uncommitted changes from one session become orphaned when another session switches context

## Rules

### 1. Stash Safety

Never `git stash pop` or `git stash apply` without verifying the current branch matches the stash source branch.

```bash
# Check stash source branch BEFORE pop
git stash list -1 --format='%s'
# Output: "WIP on branch-name: sha message"
# Verify branch-name matches current branch
```

Gate H in `context-gate-hook.py` enforces this automatically.

### 2. JSONL File Locking

All JSONL appends MUST use exclusive file locking to prevent corruption from concurrent writes. Use the shared helper, not a bare `import fcntl`: Windows has no `fcntl` module, so a direct import fails there, or finds a stand-in that does not lock (GRIP#8757).

```python
from file_lock import locked_append  # its lock comes from lib/_compat.py, so it holds on Windows too

locked_append(filepath, line)  # adds the newline if it is missing, and waits up to 5 s for the lock
```

Protected files: `gate-metrics.jsonl`, `skill-usage.jsonl`, `improvement-log.jsonl`, `metrics.jsonl`.

Shared utility: `~/.claude/lib/file_lock.py`, which `~/.claude/lib/locked_append.py` re-exports.

### 3. Working Tree Awareness

Session start detects dirty working trees and surfaces a `[DRIFT]` warning. This alerts the developer that uncommitted changes may belong to another session.

Session end logs warnings about abandoned uncommitted changes to `~/.claude/.hooks.log`.

### 4. Branch Assignment

GRIP session registry (`grip_registry.py`) assigns NATO branch names and tracks which session owns which branch. Check registry before operations that could conflict.

## Detection Signals

| Signal | Meaning | Action |
|--------|---------|--------|
| `[DRIFT] dirty:N` | N uncommitted files at session start | Review and commit or stash |
| `[STASH SAFETY]` gate deny | Cross-branch stash pop attempted | Verify intent with AskUserQuestion |
| Session-end uncommitted warning | Files left behind on exit | Check hooks.log for details |

## Origin

Gen 268: Branch consolidation revealed all three failure modes during `git stash pop` across branch bases and concurrent JSONL writes from multiple sessions.

## Version

1.0.0
