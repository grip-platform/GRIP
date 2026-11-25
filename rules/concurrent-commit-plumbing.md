# Concurrent-Commit Plumbing — Temp-Index Commits Never Touch Shared HEAD (PARAMOUNT)

Origin: 2026-06-10 overnight session. THREE independent instances in one night
(N=3 → rule, per `rules/knowledge-maturation-functor.md`):

1. The kanban builder agent committed `feat/team-kanban` while another session
   actively owned HEAD on `feat/wa-voice-notes` — zero collisions.
2. The converge-kernel agent committed `feat/converge-closed-loop` while main
   advanced mid-run — it re-seeded from the live ref and landed cleanly.
3. The main session's three-tier-classify commit: `git checkout`/`rebase`
   failed twice ("could not detach HEAD", non-FF rejections, stale-lock
   churn) on the contended tree; the plumbing path landed it first try.

## The pattern (copy verbatim)

```bash
export GIT_INDEX_FILE="$TMPDIR/my-idx"        # private index, not .git/index
git read-tree origin/main                      # seed from the LIVE remote ref
git update-index --add <paths...>              # stage only your paths
TREE=$(git write-tree)
COMMIT=$(git commit-tree "$TREE" -p "$(git rev-parse origin/main)" -m "...")
unset GIT_INDEX_FILE
git push origin "$COMMIT":<branch-or-main>     # push the SHA, explicit refspec
```

## Why it wins on a shared tree

- **Never reads or writes `.git/HEAD` or `.git/index`** — the two artefacts
  concurrent sessions fight over (`index.lock` collisions, mid-commit branch
  switches, "could not detach HEAD").
- **Always bases on `origin/main` as it is NOW** — no stale-local-main
  checkout regressions (tonight's disk-revert incident), no rebase dance.
- **Working tree untouched** — other sessions' uncommitted work is never
  staged, stashed, or clobbered; you stage only the paths you own.
- Composes with the existing protocol (`git status` first, atomic scope,
  explicit refspec push) — this is its mechanical core, not a replacement.

## When to use

- ≥2 concurrent sessions on `~/.claude` (the norm) and you need to land a
  commit on main or a branch.
- Any `checkout`/`rebase`/`cherry-pick` step fails on HEAD/index contention —
  do NOT retry the porcelain; switch to plumbing immediately.
- Background agents committing from the shared tree (git-crypt blocks
  worktrees here): this is the default commit path, not the fallback.

When the tree is genuinely single-session, normal porcelain remains fine.

## Falsification

Wrong if: a plumbing commit ever silently bases on a stale ref despite
`read-tree origin/main` after `git fetch` (mechanism defect — fix, don't
abandon); or porcelain-with-locks proves equally collision-free over ≥20
concurrent-session commits (then this is pure ceremony — demote to memory);
or the pattern produces orphaned temp indices that corrupt staging (clean-up
defect: indices live in `$TMPDIR`/job dirs, never `.git/`).

## Scope

Every multi-session commit on `~/.claude` and any git-crypt repo where
worktree isolation is unavailable. Sits beside
`feedback_concurrent_session_shared_tree_commit_protocol` (the behavioural
protocol) and Rule 13 (worktree-first where worktrees work).
