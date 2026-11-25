# Worktree Isolation Guide

## When to use worktrees

**Concurrent sessions** (2+ Claude Code instances on the same repo): MANDATORY.
Without worktree isolation, one session can switch branches mid-commit in another,
causing changes to land on the wrong branch (Gen 348 incident).

**Single session**: RECOMMENDED but not required. Direct branch work is safe when
only one Claude Code instance is running.

## Enforcement levels

Configure in `~/.claude/settings.json`:

```json
{
  "worktree_enforcement": "strict"
}
```

| Level | Gate O behaviour | Default for |
|-------|-----------------|-------------|
| `strict` | BLOCKS branch switching in main session | V>> (concurrent sessions) |
| `relaxed` | WARNS but allows branch switching | Team installs (single session) |

If not set, the default is auto-detected from `facts/identity.md`:
V>> signature = strict, all others = relaxed.

## Usage

### Create a worktree

```bash
bash ~/.claude/lib/grip-worktree.sh add /tmp/my-feature feat/my-feature
```

This does three things:
1. Creates a git worktree with `--no-checkout` (avoids git-crypt smudge filter errors)
2. Links git-crypt keys from the main `.git/` into the worktree
3. Checks out the branch safely
4. Links the worktree's Claude Code project directory to the parent repo's (shares MEMORY.md)

### Remove a worktree

```bash
bash ~/.claude/lib/grip-worktree.sh remove /tmp/my-feature
```

Cleans up the worktree, branch lock, and project context link.

**Removal refuses to discard uncommitted work.** If the worktree has
uncommitted changes to tracked files, `remove` lists them and exits non-zero
rather than deleting them. Commit or stash them, or discard them explicitly:

```bash
bash ~/.claude/lib/grip-worktree.sh remove /tmp/my-feature --force
```

Committed work is never at risk either way — `git worktree remove` leaves the
branch ref alone, so commits survive on the branch. What `--force` discards is
the working-tree edits, which have no other copy.

Untracked and gitignored files (`state/`, `data/`, `cache/` churn) are removed
with or without `--force`. The guard is deliberately scoped to tracked files:
worktrees carry that churn constantly, so refusing on it would fire on almost
every removal and train everyone to pass `--force` reflexively.

### List worktrees

```bash
bash ~/.claude/lib/grip-worktree.sh list
```

## git-crypt protocol

GRIP uses git-crypt for encrypted files. Native Claude Code worktrees
(`isolation: "worktree"`) fail silently on git-crypt repos because the
smudge filter cannot find the decryption keys.

**Always use `grip-worktree.sh`** for this repo. It handles the 3-step
protocol automatically:
1. `git worktree add --no-checkout` (no smudge filter runs)
2. Symlink/junction `git-crypt/` keys from main `.git/`
3. `git checkout` (smudge filter now finds keys)

## Windows support

On Windows (Git Bash / MINGW / MSYS / Cygwin):

- **Directory junctions** (`mklink /J`) are used instead of symlinks. Junctions
  do not require Developer Mode or administrator privileges.
- **Temp paths**: Use `$TMPDIR` (Git Bash sets this) instead of `/tmp`.
- **Junction cleanup**: `rmdir` is used instead of `rm` for removing junctions.

### Developer Mode (optional)

If you enable Developer Mode in Windows Settings, symlinks will work natively.
This is not required -- junctions work without it.

## Context linking

Claude Code creates project-specific storage under `~/.claude/projects/` based
on the working directory path. A worktree at `/tmp/feature` gets a different
project namespace from the main repo.

`grip-worktree.sh` automatically links the worktree's project directory to the
parent repo's, so worktree sessions share:
- MEMORY.md
- Session context and instance history
- Any project-specific settings

## Troubleshooting

### "Branch is locked by another session"

A branch lock file exists in `state/branch-locks/`. If the locking session is
dead (PID no longer running), the lock is auto-removed on next attempt.
Manual cleanup: `rm state/branch-locks/<branch>.lock`

### "Worktree path must be outside the repo"

Worktrees cannot be created inside the repo directory. Use `/tmp/` or `$TMPDIR`.

### git-crypt smudge filter errors

If you see `error: external filter 'git-crypt' failed`, you used native
worktree isolation instead of `grip-worktree.sh`. Remove the worktree and
recreate with the script.
