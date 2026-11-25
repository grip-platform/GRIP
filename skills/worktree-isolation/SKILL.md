---
name: worktree-isolation
description: Git worktree isolation for concurrent sessions and safe branch work
version: 2.0.0
triggers:
  - concurrent sessions detected
  - branch switching needed
  - isolation required
---

# Worktree Isolation

Concurrent Claude Code sessions in one repo share a single `.git/HEAD` — any
`git checkout` by any session flips the branch under every other session
(Rule 13, `rules/efficiency-rules.md`). Isolation means one worktree (its own
`.git/HEAD`) per concurrent session.

## The launcher does this automatically (2026-07-12)

`gg++` / `gogrip` / `grip fresh|resume|bounce` run a pre-launch isolation
gate: with ≥2 concurrent sessions and no standing choice, the launch
auto-isolates into a fresh git-crypt-safe worktree off `origin/main` under
`~/.grip-worktrees/`. Solo launches keep the shared main tree (the natural
owner of memory, channels, and daemons). Control it with:

- `GRIP_SESSION_ISOLATION=main|worktree|ask` — env override for one launch
- `grip session` — interactive wizard, persists a standing choice
- `GRIP_WORKTREE_HOME` — where session worktrees live (default `~/.grip-worktrees`)

Stale session worktrees (owner PID dead, tree clean, nothing ahead of
`origin/main`) are reaped automatically at launch — dirty or unpushed trees
are always kept.

## Manual pattern — ALWAYS via grip-worktree.sh

NEVER use raw `git worktree add` in the GRIP repo: git-crypt's smudge filter
cannot find its keys in a native worktree checkout, and the checkout fails
(Rule 13's git-crypt protocol). Use the wrapper:

```bash
bash ~/.claude/lib/grip-worktree.sh add "$HOME/.grip-worktrees/my-fix" feat/my-fix \
    --lock-mutex --base origin/main
cd "$HOME/.grip-worktrees/my-fix"
# ... work, commit, push ...
bash ~/.claude/lib/grip-worktree.sh remove "$HOME/.grip-worktrees/my-fix"
```

- `--base origin/main` bases the new branch on origin/main AS IT IS NOW —
  never on whatever branch the shared tree happens to have checked out
  (branch contamination at birth).
- `--lock-mutex` closes the TOCTOU race when several sessions create
  worktrees at the same moment.

## Caveat: code, not session ops

Operational state (memory writes, channels, daemons, `state/`) still
resolves to `$HOME/.claude` — a worktree isolates CODE work. The projects
memory directory is symlinked automatically by `grip-worktree.sh`, so
MEMORY.md continuity is preserved inside the worktree.
