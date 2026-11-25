# In-Session AGORA Convergence Where AGORA Is Not Installed (PARAMOUNT)

Origin: 2026-08-01, V>>: *"remember for all sessions, wherever AGORA is not
installed, do an in-session AGORA CI convergence — broadcast to all sessions."*
Rule-at-N=1 (operator-declared, PARAMOUNT).

Trigger instance: the four HAL SDK repos (`hal-conformance`, `hal-go`, `hal-py`,
`hal-js`) had HAPPI/1.3 PRs ready with strong local evidence, but **no AGORA and
no CI at all** — `statusCheckRollup` returned `checks=0`. Under
`ci-review-driven-convergence.md` that is absence-of-signal, not a PASS, so the
PRs stalled awaiting a human. V>>'s ruling closes that hole: the absence of an
installed reviewer is not a reason to stall, it is a reason to **run the review
in-session**.

## The Law

> **A repo without AGORA installed is not a repo without review. When a PR's
> repo has no AGORA and no CI, run the AGORA convergence IN-SESSION — a
> cross-provider council over the real diff, judged by an EXOGENOUS
> non-Anthropic judge, converging on each surviving concern — and treat that
> sealed verdict as the ratification that AGORA would otherwise have supplied.**

This does **not** weaken `exogenous-anchor-law.md`. It relocates the anchor, it
never removes it. The judge is still external to the agent proposing the change.
What changes is *where the council runs* (in-session, on demand) — never *who
adjudicates* (never the author, never self-grading).

## What "in-session AGORA convergence" means, concretely

1. **Gather real evidence first** — verify-canonical the diff on origin, plus
   the actual test/build/lint output. A council over a narrated summary launders
   a corrupt premise (`prove-it-claims-canonical.md`); ground it in the diff.
2. **Run the cross-provider council** — `bin/grip-sovereign-council` (Pool-B,
   $0, off the weekly Anthropic limit) with distinct expert lenses, NOT
   redundant voices. `lib/agora_pr_review.py` where the AGORA path is available.
3. **Exogenous judge, non-Anthropic** — the verdict must come from a provider
   that is not the author. A self-graded review is theatre.
4. **Converge, do not merely collect** — every surviving concern is an R0
   hypothesis: test it against the actual code, fix what survives, re-run.
   Verified-false concerns are recorded as refuted, never silently dropped.
5. **Seal it** — the verdict signs an IDR leaf (`cryptographic-causation.md`).
   The seal goes in the PR so the merge is auditable after the fact.
6. **Then apply the normal verdict gate** — PASS/MERGE with 0 blockers → merge,
   don't ask (CLAUDE.md "AGORA-Green = Merge"). REQUEST_CHANGES → keep
   converging. The in-session verdict carries the same authority as an installed
   AGORA's, because it is the same council and the same exogenous judge.

## What this does NOT authorise

- **Not a licence to self-approve.** No council run, or an Anthropic-judged one,
  is NOT a green. Absence of signal remains absence of signal.
- **The bootstrap carve-out still stands** — a PR that creates or widens the
  merge authority itself stays human-merged (`exogenous-anchor-law.md`).
- **Real, present, clear danger still HALTs** — security regression, secret
  exposure, guardrail removal, irreversible blast radius. Escalate, never
  self-ratify (CLAUDE.md "Pragmatism — Stuck-Loop Admin-Merge").
- **A red required check is still red.** This rule covers repos with NO
  reviewer, not repos whose reviewer is failing.

## Preferred order

Installed AGORA > in-session council > nothing. Where AGORA *can* cheaply be
installed on a repo that will see repeat PRs, installing it is the better fix —
the in-session path is the answer for one-off or unequipped repos, and the
immediate unblock everywhere else.

## Falsification

Wrong if, over the next ~10 in-session convergences: the council never once
surfaces a concern the author would have shipped (pure ceremony → demote to
advisory for low-risk diffs); OR an in-session-ratified merge causes a defect an
installed AGORA would have caught (the in-session council is weaker than claimed
→ fix the lens set or the judge routing, do not drop the rule); OR the run cost
exceeds the cost of simply waiting for the operator (scope to PRs that actually
block progress). Track: PRs stalled purely on absent-reviewer per week (target 0
after this rule); defects reaching main via in-session ratification (target 0).

## Scope

Every repo, every session, whenever a PR is merge-ready but its repo has no
AGORA and no CI. Sits beside `ci-review-driven-convergence.md` (the verdict
gate), `exogenous-anchor-law.md` (the anchor it relocates but preserves),
`prove-it-claims-canonical.md` (ground the premise), and
`cryptographic-causation.md` (seal the verdict). Mirror into `~/.hal/rules/`.
