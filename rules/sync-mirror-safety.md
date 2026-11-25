# Sync / Mirror Safety: Never Single-Step a Push to a Shared Repo (PARAMOUNT)

## The failure shape

An automated sync/mirror to a shared target (public OSS repo, upstream,
deploy mirror) does, in effect:

```
build_new_tree_from_source  →  git add -A  →  git push <shared>
```

If `build_new_tree_from_source` **silently fails or under-produces**, the
working tree is empty (or near-empty). `git add -A` then stages the
**deletion of everything the target had**. `git push` destroys the target.

The failure is invisible by default because:

- **Pipes mask exit status.** `producer_that_failed | tar -x` returns
  *tar's* exit code. `tar` succeeds on empty input. The pipeline exits 0
  and *looks* successful.
- **Mass deletion looks like a normal diff.** `181 files changed,
  34867 deletions(-)` is a valid-looking git status. Nothing flags it
  as a wipe unless you specifically check the insertion/deletion shape.

## Root cause (the originating instance)

An unquoted/misquoted variable that fails to word-split silently turns a
multi-path operation into a single-bogus-path no-op. This generalises
beyond `git archive`: any `cmd -- $VAR_OF_PATHS` is exposed.

## The mandate (mechanical, non-negotiable)

Any operation that pushes/syncs to a **shared or public** repo MUST:

1. **Be two-step: build → verify → push.** The push is a *separate*
   invocation from the build. Never `build && git add -A && git push`
   in one breath to a shared target. The verify step exists so a broken
   build cannot reach `push`.

2. **Run a mechanical mass-deletion guard before `push`.** Compute the
   staged `--numstat`. **ABORT** if `insertions == 0`, or if
   `deletions ≫ insertions` and absolute deletions are large (the
   repo-wipe signature). A real catch-up of N PRs is addition/
   modification-shaped, never thousands of pure deletions.

3. **Assert on produced CONTENT, not pipe exit status.** After the build,
   assert the extracted file count exceeds a sane threshold (e.g. a repo
   with 200 tracked files must yield > ~120). Exit 0 through a pipe is
   not evidence the source produced anything.

4. **Allowlist, never denylist, for what enters a public repo.** Copy
   only known-safe paths into the target. "Copy everything minus
   exclusions" leaks the next internal file someone adds. Re-assert,
   on the built tree AND the remote after push, that forbidden paths
   are absent.

5. **Hardcode or array critical pathspecs.** Do not pass a multi-path
   set through an unquoted shell variable. Use explicit literal args or
   a bash array (`"${PATHS[@]}"`). Verify the pathspec matched.

**Positive (reinforce, do not only record the failure):** the
verify-before-push two-step with diff review is exactly what prevented
the disaster. It is not optional ceremony — it is the control that
turns a silent repo-wipe into a caught build bug. Keep it for every
shared/public push, always.

## Recovery protocol

If the mass-deletion guard fires (insertions==0 / deletion-dominant):

1. Do **not** push. The build is broken, not the target.
2. Diagnose the source step (pathspec match? variable split? empty
   producer? wrong cwd/ref?). Fix the build.
3. Rebuild into a fresh tree, re-run all guards (content count,
   forbidden-absent, secret scan, mass-deletion).
4. Only push when the delta is addition/modification-shaped and every
   guard passes.

## Falsification

This doctrine is wrong if, over the next 20 shared/public syncs:

- The two-step + mass-deletion guard never once prevents a bad push AND
  measurably slows delivery (pure overhead, no catch) — then relax to
  guard-only, single-step.
- The mass-deletion guard false-positives on legitimate large-deletion
  syncs (e.g. an intentional repo pruning) more than it catches real
  wipes — then the heuristic threshold is mis-tuned (raise it), do not
  drop the guard.
- A repo-wipe still reaches a shared target *despite* the two-step —
  then the guard logic itself is defective and must be hardened, not
  abandoned.

## Scope

Every push/sync to a shared or public git target: OSS-mirror catch-ups,
fork syncs, deploy-mirror pushes, `git subtree`/`git archive`-based
projections, any scripted `git add -A && git push <not-your-private-branch>`.

Outside scope: pushing your own feature branch to your own private repo
(still benefits from the mass-deletion guard, but the two-step is not
mandatory there — blast radius is your branch, recoverable).

## Relationship to other rules

Sits beside `dispatch-economics.md` and `agent-dispatch-protocol.md` as a
mechanical safety doctrine. Where those govern *delegation* envelopes,
this governs the *shared-write* envelope. Same character: a universal,
falsifiable, habit-and-review-enforced discipline, no mechanical hook
required (a `sync-guard` PreToolUse hook may follow once enough
shared-push callsites exist to justify it).

Narrative (origin, incidents, measurements): `docs/doctrine/sync-mirror-safety.md`.
