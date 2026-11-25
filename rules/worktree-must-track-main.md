# A Worktree Must Be Born From Main, and Stay Level With It (PARAMOUNT)

Origin: 2026-08-19, V>>: *"Before starting ANY ISOLATED WORKTREE, the WORKTREE
must pull local MAIN!!!! … Extend the rule, periodically update the shared
worktree by merging main into the tree, and merge main into the current tree a
GRIP session uses."* Rule-at-N=1 (operator-declared), with the shim shipped
alongside because the failure is silent by construction.

## The law

> **An isolated worktree is created from a FRESHLY FETCHED `origin/main`, and is
> merged back level with `origin/main` periodically for as long as the session
> lives. Both halves are required: being born fresh does not keep you fresh.**

This binds the session worktree AND the shared tree (`~/.grip`), for different
reasons. The session tree is where the work happens. The shared tree is where
**launchd agents execute from**, so a stale shared tree is an unreviewed deploy
(`shared-tree-is-not-canonical.md`).

## Why `--base origin/main` was not enough

The launcher already passed `--base origin/main`. That is correct in shape and
can be arbitrarily wrong in fact, because **`origin/main` is a local
remote-tracking ref that moves only on `git fetch`**. With no fetch it points
wherever the last one left it. On the originating session the reflog showed
`origin/main` frozen at `1f28e297` until a mid-session fetch moved it to
`66d1f220` — so the "fresh" base was two commits stale at birth and eight behind
by mid-session.

The previous behaviour was a deliberate, documented trade-off, not an oversight:
AGORA #3939 round 2 removed the fetch to save launch latency, reasoning that
*"freshness of the base is not load-bearing"*. That premise is what this rule
refutes.

## Why staleness is not cosmetic

It corrupts what the tests are grading. Much of GRIP's suite loads modules via
`CLAUDE_DIR/lib`, and `CLAUDE_DIR` defaults to `~/.claude` → `~/.grip` — the
**shared tree**, never a worktree. Whichever test imports a module first owns
the `sys.modules` slot for the whole session, and modules that import each other
internally (`resolve_model` does its own `import grip_harness`) will bind
whichever copy won.

So a stale worktree produces a suite where **assertions read one copy of a module
and the code under test reads another**. On the originating session that masked a
genuinely failing test through a full round of green. It is invisible in
isolation, because alone nothing else has claimed the slot yet — which is
`tests-must-be-able-to-fail.md` shape 2, arriving through the environment rather
than through a hardcoded path.

## Mandate

1. **Fetch before basing.** The pre-base fetch is capped and **fail-open**: a
   timeout, an offline machine or any git error falls through to the
   last-fetched ref. A launch is never blocked on the network — it starts from a
   fresher base whenever the network allows, which is strictly better than never
   trying. The latency objection is answered, not dismissed.
2. **Re-level periodically.** `bin/grip-tree-sync` merges `origin/main` into a
   tree and is **idempotent** — a level tree merges nothing and exits 0, so it is
   safe on a timer (f∘f = f). `--shared` also covers `~/.grip`; `--check`
   reports without merging and exits 1 when behind — or when the tree holds
   commits that exist on **no remote branch**, since work on exactly one
   machine is the failure a status check exists to surface. It reports
   ahead-ness and uncommitted files alongside, so "level with origin/main"
   means level and nothing else.
3. **Decline rather than risk.** A dirty tree is reported, never merged over:
   merging into uncommitted work is how a session loses changes it never chose
   to risk. A conflicting merge is **aborted**, leaving the tree untouched
   rather than half-merged behind a timer-driven run.
4. **Measure the drift before blaming the diff.** When a worktree behaves
   differently from expectation, `git rev-list --count HEAD..origin/main` before
   debugging the code.

## Falsification

Wrong if, over the next ~20 isolating launches: the pre-base fetch measurably
delays launches without any of them starting from a materially fresher base
(then the AGORA #3939 latency argument stands and this should degrade to a
background refresh); OR periodic re-levelling produces more merge conflicts than
the drift it prevents costs (then re-level on demand rather than on a timer,
never drop the birth-fetch); OR no stale-worktree defect recurs in a quarter
with the fetch REMOVED (then staleness really was not load-bearing — refuted
today). Track: worktrees born behind `origin/main` (target 0) and
decision-gating results later found wrong because two trees disagreed (target 0;
was 1 on 2026-08-19).

## Scope

Every isolated worktree on every machine, and the shared tree that launchd
agents execute from. Sits beside `shared-tree-is-not-canonical.md` (why the
shared tree lies), `concurrent-commit-plumbing.md` (worktree-first),
`tests-must-be-able-to-fail.md` (the masking this causes), and
`verify-canonical-not-claimed.md` (the parent doctrine). Shim:
`bin/grip-tree-sync` · `lib/grip_session_wizard.py::_base_args` ·
`tests/test_grip_tree_sync.py`.
