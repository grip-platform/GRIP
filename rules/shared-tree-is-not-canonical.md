# The Shared Tree Is Not Canonical (PARAMOUNT)

Rule-at-N=3 per `knowledge-maturation-functor.md`, with the mechanical shim shipped
alongside because the failure is silent by construction and habit cannot catch it.

## The failure

`~/.claude` is a symlink to a shared working tree (`~/.grip`), and launchd agents
execute code **from that tree** — observed 2026-08-03: `com.grip.channel`,
`com.grip.cos`, `com.grip.heartbeat`, `com.grip.mesh-bus`, `com.grip.mesh-client`.
Whichever branch an interactive session last left checked out therefore decides two
separate things at once:

1. **what production runs**, and
2. **what any verification read returns**.

The tree is a perfectly valid git checkout
answering correct questions about the wrong ref, so every probe reads as healthy.
This is the same class as the silent-failure family fixed the same week
(`tests-must-be-able-to-fail.md`): the system runs, reports, and is wrong.

## The law

> **A read from the shared tree is evidence of what some branch contains, never of
> what GRIP canonically contains. Any claim that gates a decision — "the fix
> landed", "the function is missing", "there were N commits" — resolves against
> `origin/main`, not against `~/.claude` or `~/.grip`.**

This is `verify-canonical-not-claimed.md` applied to the *tree itself* rather than
to a status label. That rule already says trust `origin/main` over a dirty local
tree; this names the specific, non-obvious reason the local tree lies even when it
is perfectly clean — it is clean, and on the wrong branch.

## Mandate

1. **Resolve decision-gating reads against `origin/main` explicitly** —
   `git show origin/main:<path>`, `git log origin/main …`. A bare `git log` or a
   plain `import` inside the shared tree is scoped to whatever is checked out.
2. **Run the check before trusting the tree** — `bin/grip-tree-check` exits `0` on
   main and level, `1` off main, `2` behind, and `3` when git could not answer.
   **`3` is deliberately not `0`**: "unknown" must never be indistinguishable from
   "fine", which is the failure mode this whole rule exists to close.
3. **Leave the shared tree on `main`.** Feature work belongs in a git-crypt-safe
   worktree (`lib/grip-worktree.sh`), which is already the standing rule for
   concurrent sessions — this adds the reason it matters even for a *single*
   session: production reads that tree. `hooks/shared-tree-write-advisory.py`
   (deciding through `lib/shared_tree_write_advisor.py`) says so whenever a
   session working outside the shared tree writes into it, at advisory tier
   only: it adds context for the session and a line for the operator, takes no
   permission decision and never blocks the write.
4. **A stale shared tree is a production incident, not a housekeeping nit.** Eight
   days of launchd agents running feature-branch code is an unreviewed deploy.

## Falsification

Wrong if, over the next ~10 sessions: `grip-tree-check` never once reports a
non-canonical tree that mattered (the arrangement is safe in practice — demote to
advisory); OR the shared tree stops being the execution root for launchd agents
(the blast radius disappears — narrow to the verification-read half only); OR the
check's `OFF_CANONICAL` fires so routinely on deliberate, short-lived checkouts that
it is ignored (tune to warn only past a duration threshold — never drop it). Track:
decision-gating claims later found wrong because they were read from a non-canonical
tree (target 0; was 3 on 2026-08-03).

## Scope

Every read of `~/.claude` / `~/.grip` that gates a decision, and every process that
executes from it. Sits beside `verify-canonical-not-claimed.md` (the parent
doctrine), `concurrent-commit-plumbing.md` (worktree-first), and
`tests-must-be-able-to-fail.md` (the silent-failure family). Shim:
`bin/grip-tree-check` · `lib/shared_tree_state.py` ·
`tests/test_shared_tree_state.py`.

Narrative (origin, incidents, measurements): `docs/doctrine/shared-tree-is-not-canonical.md`.
