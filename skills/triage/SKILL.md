---
name: triage
description: Safely triage lingering feature branches — classify each as already-on-main, fast-forward, needs-PR, or diverged, then act. Use when `git worktree list` + `git branch -a` have accumulated unmerged work you want to resolve without losing commits. Named "triage" rather than "merge" to avoid collision with HAL's REPL command surface and with the `git merge` verb.
---

# /triage — Safe Branch Triage

One-command resolver for the "too many leftover branches" problem.
Every branch gets classified, then acted on without data loss.

## Usage

```bash
/triage                   # triage every non-main branch, act per class
/triage feat/foo          # triage a single branch
/triage --dry-run         # classify only, don't act
/triage --repo ~/.claude  # operate on a different repo than cwd
```

## Classification

For each feature branch (local or `origin/*`), compute:

| Class              | Detection                                                                                  | Action                                                                                                         |
|--------------------|--------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------|
| `ALREADY_ON_MAIN`  | Every unique commit's subject already appears in `git log main --grep`                     | Delete local + remote branch. Content is on main (usually via squash-merge); the branch is redundant.          |
| `FAST_FORWARD_MAIN`| `merge-base --is-ancestor BRANCH main` — i.e., main already contains every commit on BRANCH| Delete local + remote branch. It's the trivial case of above.                                                  |
| `FAST_FORWARD_PR`  | `merge-base --is-ancestor main BRANCH` — main is the ancestor; BRANCH is strictly ahead    | Open PR + `gh pr merge --admin --squash --delete-branch`. Genuinely new work that can land cleanly.            |
| `DIVERGED_SAFE`    | Local and remote have both-unique commits BUT every local-unique subject is on main        | Same as `ALREADY_ON_MAIN` — squash-merged under a different SHA. Push a `-local-preserved` backup, then delete.|
| `DIVERGED_UNSAFE`  | Both sides have unique commits AND at least one local-unique subject is NOT on main        | HALT. Print the unique commit list. Require operator to rebase / cherry-pick / decide.                          |
| `WIP_ANNOTATED`    | Branch has at least one commit with "wip", "superseded", or "abandoned" in the subject      | Leave on origin (don't delete). This is intentionally preserved WIP.                                           |

## Execution

When `/triage` is invoked:

```bash
# 1. Enumerate branches (excluding main + current)
CURRENT="$(git branch --show-current)"
git for-each-ref --format='%(refname:short)' refs/heads refs/remotes/origin \
  | grep -vE "^(main|origin/main|origin/HEAD|origin/${CURRENT}|${CURRENT})$"

# 2. For each branch B, gather:
AHEAD=$(git rev-list --count main..B)
BEHIND=$(git rev-list --count B..main)
SUBJECTS=$(git log --format=%s main..B)

# 3. Classify via the table above.

# 4. Act per class (unless --dry-run):
#    - DELETE: `git branch -D B` and `git push origin --delete B` if remote exists.
#    - MERGE: `gh pr create ... && gh pr merge <n> --admin --squash --delete-branch`.
#    - PRESERVE: `git push origin refs/heads/B:refs/heads/B-local-preserved`, then delete.
#    - HALT: print the unique subject list and break.

# 5. Report per-branch verdict + action taken.
```

The handoff is a table:

```
BRANCH                                CLASS             ACTION
feat/dfi-fib-followup                 FAST_FORWARD_PR   PR #2306 merged, branch deleted
feat/dfi-fib-sprint-local-preserved   DIVERGED_SAFE     Deleted (content on main via PR #2300)
feat/hal-provider-routing-tool-loop   WIP_ANNOTATED     Kept on origin (marked superseded)
feat/foo-experiment                   DIVERGED_UNSAFE   HALTED — 2 local-unique commits need review
```

## What It Does NOT Do

| Not done by `/triage`                         | Handled by          |
|-----------------------------------------------|---------------------|
| Remove git worktrees                          | `/branch-cleanup` / `git worktree prune` |
| Stage / commit uncommitted changes            | `/shipit`           |
| Force-push or rewrite history                 | Never — unsafe      |
| Auto-resolve merge conflicts                  | Operator            |
| Delete `main`, `origin/main`, or current branch| Hard-excluded by enumeration regex |
| Act on branches matching `wip/*`, `spike/*`, or with WIP-annotated commits | Left alone by default |

## Safety Invariants

1. **Never delete without a remote copy.** Local-only branches are first pushed to `origin/B-local-preserved` before deletion. The preservation branch is deleted only after the operator confirms (or automatically when content is verified on main via subject-grep AND SHA match).
2. **Never force-push main.** `gh pr merge --admin --squash` is the only path to main. No direct `git push origin main`.
3. **Never touch branches matching regex** `^(main|origin/main|origin/HEAD)$` or the current branch.
4. **Always preserve WIP-annotated branches.** A commit subject containing `wip`, `superseded`, `abandoned`, or `draft` signals intentional preservation — skip.
5. **HALT on `DIVERGED_UNSAFE`.** Don't guess the operator's intent when commits genuinely conflict in history. Print and stop.

## When to Use

- After a long sprint that left many feature branches behind.
- When `git worktree list` shows orphans alongside real work.
- When `/shipit` gets refused because "branch already on main via squash."
- During end-of-week hygiene (paired with `/branch-cleanup` for worktree GC).

## Falsification

This skill is wrong if any of the following hold after three months of use:

- The `ALREADY_ON_MAIN` detection misses >10% of squash-merged branches (operator manually re-checks and deletes).
- `DIVERGED_UNSAFE` false-positives on branches that are actually safe to delete (operator bypasses via `--force-delete`).
- The execution path ever deletes a branch whose commits weren't verified on main AND weren't preserved to origin first.

If falsified, tighten the classification heuristics or split the triage step into a separate `/triage-plan` skill that writes a proposed actions file for manual review.

## Integration Notes

- Reuses `gh pr create` + `gh pr merge` — no fork of grip-platform/GRIP workflow.
- Subject-grep uses `git log main --grep="^<subject>$"` with `^` and `$` anchors to avoid partial matches.
- Safe on git-crypt repos (never touches `.gitattributes` or encryption keys).
- Compatible with `/closeout` — run `/triage` first to resolve branches, then `/closeout` for session wrap-up.

## Origin

Codified from the 2026-04-18 triphase-complete cleanup (HAL+GRIP, 41
worktrees → 3, 3 preservation pushes, 1 PR merged, 1 remote branch
deleted). The manual workflow surfaced four invariants — preserve
before delete, classify before act, halt on ambiguity, never force —
that became this skill's safety envelope.
