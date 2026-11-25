# CI/Review-Driven Convergence (PARAMOUNT)

Origin: 2026-05-15. PR #2723 (relay-dispatcher IDR, #2720). V>> directive:
*"are you monitoring the CI? /converge monitor, address AGORA concerns and
suggestions, and fix and push until it's a definite pass. ... this pattern
should be codified — relying on CI/CD reviews to drive local
self-improvement work loops."* The trigger: a session that opened a PR and
then said "monitor CI; nothing actionable until it reports" — passive
waiting instead of driving the loop.

## The doctrine

Opening a PR is **not** the end of a deliverable — it is the start of a
convergence loop whose criterion is **external**. After any PR is opened
(or pushed to), do not passively await CI/review. Actively converge:

```
converge(
  state      = the PR,
  criterion  = CI green AND council review a definite PASS,
  improve    = correctness-by-construction fix of each concern,
  max_depth  = auto-fibonacci increment (1,1,2,3,5,8…),
)
```

Poll CI + the council verdict → treat every failure / concern / suggestion
as a fix input → fix → commit → push → re-poll → repeat, increasing depth
on each non-converged iteration, until CI is green **and** the council
(HAL Council on CodeTonight repos; AGORA descriptive review; CodeRabbit on
a client repo) is a definite PASS.

## Why this is Goodhart-resistant (the load-bearing point)

The convergence criterion is the **external** review verdict + CI, **not**
a self-assigned score. This structurally avoids the 91-attractor and the
fluency feedback-loop that `/converge`'s own Pattern Check warns about: you
cannot talk your way to a pass. CI either runs the tests or it does not;
the council either blocks or it does not. The loop's improvement signal
comes from outside the model producing the work.

This only holds while the tests themselves are Goodhart-proof (Rule 14):
each must fail under an obvious mutation. "CI green" is a real criterion
only if green can go red. A suite that cannot fail makes this doctrine a
rubber stamp — then the criterion is illusory and the loop is theatre.

## R0 discipline inside the loop (mandatory)

Reviewers (human or council) frequently work off **truncated diffs** or
partial context. Every concern is a hypothesis, not a fact. Before acting:

1. **Verify the concern against the actual code**, not the diff the
   reviewer saw. (#2723: HAL Council "BLOCK — `_LIB` undefined / circular
   import" was false — `_LIB` *was* defined; `relay_decision` imports only
   stdlib. The reviewer misread a truncated `_IMPERATIVE_WORDS`.)
2. **Real issue → fix correctness-by-construction.** (#2723: the
   imperative-verb set genuinely *was* incomplete — "Stop the deployment"
   mis-binned. Fixed + regression test.)
3. **Artefact / false positive → refute with EVIDENCE, not argument.**
   Add a test that demonstrates the concern cannot occur (#2723: a
   subprocess test that actually imports `relay_dispatcher` and asserts it
   resolves — the blocker dies on evidence). A reply that just says "the
   reviewer is wrong" does not converge; a passing test does.
4. **Sound design the reviewer dislikes → defend with reasoning.** Do not
   cargo-cult-comply. (#2723: separate `relay_decision.py` module is
   correct SRP, not "scattering"; `ts` is intentionally wall-clock
   metadata, not non-determinism.)
5. **KISS/YAGNI still bind.** Reviewer suggestions can be
   over-engineered. (#2723: `imperatives.json` config-loader and a
   `redact_field` decorator were YAGNI — answered with a curated frozenset
   + a structural test, not machinery.) "Address the concern" means
   *resolve it*, which includes a reasoned, tested refusal.

The instinct behind a wrong-reasoned block is often right (the import path
*was* untested even though the stated cause was false). Mine the instinct,
discard the bad reasoning, ship the test that closes the real gap.

## Verdict-gated convergence (2026-05-21, V>>-ratified)

AGORA reviews now carry a top-level 3-status verdict synthesised by an
LLM-as-judge (Google Gemini 3.1 Pro Preview — exogenous to the 3
speakers per `exogenous-anchor-law.md`). The verdict drives the loop:

| Judge verdict | Loop behaviour | Why |
|---|---|---|
| **MERGE** | Exit loop. Merge without asking (`CLAUDE.md` "AGORA-Green = Merge"; an RSI loop admin-bypasses per the merge-gate carve-out). | No blockers OR blockers exclusively cosmetic. |
| **REQUEST_CHANGES** | Continue loop: R0 each concrete concern → fix → push → re-run AGORA. Max 5 fix iterations. | Real blockers that would ship a regression. |
| **DEFER_CHANGES** | Exit loop. AGORA auto-creates `sprint-loop` follow-up issue with PR back-link. Surface the follow-up in one line, then merge (`CLAUDE.md` "AGORA-Green = Merge"). | Worth addressing but not blocking ship (scope-bleed, future-proofing, suggested refactors). |

**Token-savings rationale**: prior to this rule, every Speaker C concern
triggered a fix iteration even when DEFER-class (suggestions, scope-bleed,
future-proofing). Verdict-gating eliminates fix-cycles on those classes.
Estimated saving: ~40% iteration count over 30-PR window — R0-falsifiable
post-rollout via hypothesis H-AGORA-VERDICT-ITER-1 (deadline 2026-07-20).

**Fail-closed contract**: if the judge times out / errors / returns
malformed verdict / fails auth, AGORA renders a "Judge unavailable"
banner with REQUEST_CHANGES fallback (loop continues, never merges
silently). A self-fulfilling judge from a speaker provider is strictly
worse than no judge.

## Mechanical loop (CodeTonight repos)

- `gh pr checks <n> --repo <org>/<repo>` — per-check pass/fail
  (Projects-classic-safe; not `gh pr view` default — see
  `operational-context.md`).
- Council verdict: `gh api repos/<org>/<repo>/issues/<n>/comments`
  (`github-actions[bot]` body) — HAL Council BLOCK lists confirmed
  blockers; AGORA posts the verdict-gated descriptive review.
- AGORA verdict extraction: parse the latest `github-actions[bot]`
  comment whose body starts with `## AGORA Council Review` for the
  `> **Verdict: <TOKEN>**` blockquote. Branch the loop per the table
  above.
- Fix in the **isolated worktree** (Rule 13), `grip-test` green locally
  before push, atomic add+commit (push is a separate command — the
  PreToolUse push gate blocks the *whole* Bash command if `git push`
  appears with a bare/again-protected refspec; use
  `git push origin <feature-branch>` explicitly).
- Push → CI re-runs → re-poll. Use the CI wall-clock (minutes) for
  parallel productive work (codify, next deliverable) — never idle-spin,
  never tight-sleep-poll. A long-interval re-poll or a Monitor until-loop
  on the check status is the correct waiting primitive.

## Depth / termination

Auto-fibonacci depth increments per non-converged iteration. Termination:
CI green + council PASS (success), OR depth cap reached with the remaining
blocker escalated to the operator with the evidence gathered. Max 3–5 fix
iterations before escalate (mirrors the improvement-cycle "max 3 fix
iterations per PR" rule). The exception is `CLAUDE.md` "Pragmatism --
Stuck-Loop Admin-Merge" (`operational-principles.md` Rule 7): a loop stuck
(>30 min or ≥3 fix iterations) on concerns verified false, on overreach or on
a flaky reviewer, with required CI green and no real, present, clear danger,
is admin-merged rather than escalated. A council that blocks on a verified
false positive is that case -- do not loop forever against a broken reviewer,
and do not hand the merge to the operator either. Escalation is for a real
blocker, or for one of the dangers Rule 7 names.

## Merge gate: RSI-loop admin-bypass vs default (V>>-ratified 2026-05-17)

Definite PASS (CI green + council PASS) is the convergence criterion. What
happens *at* definite PASS depends on the actor:

- **Autonomous RSI / `/auto` / overnight loop**: definite PASS → the
  loop's terminal action is **admin-bypass auto-merge**
  (`gh pr merge <n> --admin --squash`), per the RSI-loop carve-out
  predicate in `exogenous-anchor-law.md`. Passive-await-human-merge would
  dead-lock the loop — the loop's push identity cannot satisfy GitHub's
  "approving review by a non-author" rule. The cross-provider council + CI
  *is* the exogenous edge; the human merge is not required as the anchor.
- **Interactive / non-RSI work**: definite PASS (AGORA PASS/MERGE or
  DEFER_CHANGES, at least two seats sitting, 0 blockers, REQUEST_CHANGES from
  none of them; a DEFER files its follow-up and is surfaced in one line) →
  **merge without asking** — AGORA is the
  exogenous ratification (V>> standing policy 2026-05-30: "if AGORA is
  green … I don't need to be asked. This blocks progress."). Do NOT ask
  "merge or hold?". Superseded the prior "surface for human merge" default.
  See CLAUDE.md "AGORA-Green = Merge" + `operational-principles.md` Rule 6.
- **Bootstrap-excepted PR** (modifies the merge authority itself):
  human-ratified + human-merged only — never loop-merged under its own
  new authority.

## Falsification

This doctrine is wrong if, over the next ~10 PRs it governs:

- It produces churn without convergence (re-review oscillates, no PR
  reaches a stable PASS) → the criterion is mis-set or the council is
  non-deterministic; fix the council, do not abandon external-criterion
  convergence.
- It induces gaming — code written to please the reviewer rather than be
  correct — detected by Goodhart-proof tests passing while real behaviour
  regresses → tighten the tests; the defence is mutation-survival, not
  reviewer sentiment.
- Main-thread polling cost exceeds the value of catching the issues
  (the loop spends more than it saves) → widen poll interval / batch, do
  not drop the loop.

Track via: PRs reaching definite PASS within ≤3 fix iterations (target
≥80%); council-false-positive rate (informs whether the reviewer needs
fixing); zero "opened PR then went passive" sessions.

## Scope

Every PR-producing workflow: `/converge`, `/ultrado` SHIP→MEASURE,
`/sprint`, `/rsi`, `/auto`, manual PRs. Sits beside
`agent-dispatch-protocol.md` and `dispatch-economics.md` as a universal,
falsifiable, habit-and-review-enforced doctrine. The recovery-protocol
relationship: agent-dispatch-protocol governs *getting work done*;
this governs *getting it past the external gate*. A deliverable is not
"done" at PR-open; it is done at CI-green + council-PASS.
