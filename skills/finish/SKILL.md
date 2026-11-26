---
name: finish
description: One-shot finish-a-feature lifecycle orchestrator. Takes an in-flight feature/PR/branch and drives it to DONE — verify-canonical the real state, finish/upgrade the work, optional novel delight++, seal, PR with a plain-language body, AGORA-review with verdict-gated convergence, then AGORA-green merge (bootstrap-class PRs human-merged) + deploy + verify-canonical the deploy + anti-drift close. Use when the operator invokes /finish, says "finish this off / drive this to done / win this PR / close this out properly", or hands a half-finished feature/branch/PR to take across the full lifecycle in the least-token way. Thin orchestrator — composes ultrado / auto / converge / efficient / dispatch and the ci-review-driven-convergence + verify-canonical rules; reimplements none of them.
status: Active
version: 1.0.0
created: 2026-06-22
triggers:
  - /finish
  - "finish this off"
  - "drive this to done"
  - "win this PR"
  - "close this out properly"
  - "take this feature to done"
integrates:
  - ultrado/SKILL.md
  - auto/SKILL.md
  - converge/SKILL.md
  - efficient/SKILL.md
  - dispatch/SKILL.md
see_also:
  - go
  - sprint
  - create
---

# /finish — One-Shot Finish-a-Feature Lifecycle

Take one in-flight piece of work (a feature, a branch, a PR) and drive it all
the way to **DONE** — finished, sealed, reviewed, merged, deployed, verified —
at the **lowest token cost** while making **maximum use of GRIP's convergence
kernel**. This skill is a **thin orchestrator**: it composes existing kernels
and rules, and reimplements none of them.

**Plain English**: you point `/finish` at a half-finished thing. It first
checks what is *actually* true about that thing (never trusting a status
label), finishes the work, optionally adds one tasteful delight, commits and
pushes, opens a PR written so a non-coder understands it, gets the AGORA
council to review it, fixes anything real the council flags, and — once the
human (V>>) approves — merges, deploys, and confirms the deploy is live. Then
it tells you what is left.

**Glossary**: verify-canonical = trust origin/main + the live endpoint + an
e2e probe, never a status label / agent summary / truncated diff |
AGORA = GRIP's cross-provider PR review council | verdict = AGORA's top-level
MERGE / REQUEST_CHANGES / DEFER_CHANGES decision | CBC = correct-by-construction
| delight++ = a small novel pleasing capability | anti-drift = the "What's Up
Next" close-out block.

---

## What this skill does NOT do (DRY — compose, never duplicate)

Every heavy behaviour is **imported as-is** from a canonical kernel. `/finish`
only sequences them and adds the verify-canonical reflexes at the seams.

| Need | Imported from — do NOT restate its logic |
|------|------------------------------------------|
| Least-token execution discipline | `skills/efficient/SKILL.md` (tier-0 retrieval, depth-0 bias, single-pass CBC, batched calls) |
| Finish / upgrade the work | `skills/ultrado/SKILL.md` (recursive falsification engine) or `skills/auto/SKILL.md` Shape A/B |
| Novel delight++ under a real criterion | `skills/converge/SKILL.md` (kernel) with a CBC + anti-theatre criterion |
| Whether to delegate / fan out at all | `skills/dispatch/SKILL.md` + `rules/dispatch-economics.md` (the economic gate) |
| PR convergence loop | `rules/ci-review-driven-convergence.md` (poll → R0 each concern → fix → re-poll, verdict-gated) |
| Real-state verification | `rules/verify-canonical-not-claimed.md` |
| Merge / deploy gate | `rules/exogenous-anchor-law.md` + `rules/operational-principles.md` Rule 6 |

If a step here seems to need new logic, you are doing it wrong — find the
kernel and call it.

---

## The lifecycle (copy this checklist)

```text
- [ ] 1. VERIFY-CANONICAL the target's real state (not its status)
- [ ] 2. FINISH / UPGRADE the work to completion
- [ ] 3. (optional) NOVEL DELIGHT++ via /converge with a CBC + anti-theatre criterion
- [ ] 4. SEAL — atomic commit, explicit-refspec push, never force
- [ ] 5. PR — plain-language body (what + why before how)
- [ ] 6. AGORA REVIEW — verdict-gated convergence (max 3-5 fix iterations)
- [ ] 7. MERGE (AGORA-green; bootstrap-class human-merged) + DEPLOY + verify-canonical the deploy
- [ ] 8. ANTI-DRIFT close ("What's Up Next")
```

### Step 1 — Verify-canonical the target (NEVER trust status)

The single most load-bearing step. Before touching anything, establish what is
*actually* true — per `rules/verify-canonical-not-claimed.md`. A status label,
a merge notice, an agent summary, or a stale local tree is **telemetry, never
truth**.

- **PR target** → `gh pr view <n> --repo <org>/<repo> --json state,mergeable,headRefName,mergedAt`
  (REST fallback per `rules/gh-repo-resolution.md` if GraphQL deprecation bites).
  Also `git ls-remote origin 'refs/heads/<branch>*'` for branch existence.
- **Branch target** → `git -C <tree> log --oneline origin/main..<branch>` for the
  real delta; `git show origin/main:<path>` to confirm what already landed.
- **Feature target** → probe the live endpoint / re-run the named test; do not
  read intent from a task description.

**Three outcomes branch the whole run** (this session proved each is real):

| Reality | Action |
|---------|--------|
| Already merged / shipped | STOP. Report "already done"; jump to Step 8 anti-drift. Do not re-do it. |
| Duplicates other in-flight work | STOP. Surface the duplicate; close one, never ship both. |
| Genuinely open + incomplete | Proceed to Step 2. |

Skipping this step is how a session "finishes" a PR that was merged hours ago,
or ships a branch that duplicates already-landed work. Spend the one or two
tool calls.

### Step 2 — Finish / upgrade the work

Drive the remaining work to completion. **Do not write a new loop** — invoke
the kernel that fits the gap size, all under `skills/efficient/SKILL.md`'s
least-token discipline (tier-0 retrieval before any read, depth-0 main-thread
bias, single-pass correct-by-construction edits, batched tool calls):

- **Small, surgical finish** → do it inline at depth-0 (the `/go` shape), or
  `/converge ... criterion: "<the done-predicate>"`.
- **Multi-file / multi-wave finish** → `/ultrado` (CAREFUL precision for
  surgery, VERIFY for claims) or `/auto wave-mode --no-ship`.
- **Delegation decision** → run the `skills/dispatch/SKILL.md` gate FIRST;
  most finish-off work clears at depth-0 and must NOT be fanned out
  (`rules/dispatch-economics.md` — the most token-efficient agent is no agent).

Quality gates (DRY / KISS / CC / BIG-O / no-stubs) fire throughout — they make
the code better, not slower. Completeness (`rules/no-stubs.md`): finish the
whole input domain, never a partial-domain stub.

### Step 3 — Novel delight++ (optional, gated)

If — and only if — a small novel capability would genuinely delight on this
surface, add ONE via `/converge` with a **CBC + anti-theatre criterion**:

```text
/converge "<delight idea>" criterion: "CBC: works first-try AND
  changes something for someone OUTSIDE this building (a user/payer/
  non-operator) — not just 'makes GRIP feel more capable'"
```

The anti-theatre half is `rules/loop-priority-external-over-theatre.md`: a
delight that only makes the system feel clever is theatre — drop it. Skip this
step entirely when the honest answer is "nothing external changes." Optional by
design; never force a delight to justify the run.

### Step 4 — Seal

Atomic add + commit per coherent unit (`feedback_commit_push_often.md`), then
push on an **explicit refspec**, **never force**:

```bash
git -C <tree> add <paths>
git -C <tree> commit -m "$(cat <<'EOF'
type(scope): description

Primary Author: <operator signature>
EOF
)"
git -C <tree> push origin <feature-branch>
```

Fill `<operator signature>` with what `bin/grip-operator-signature` prints.

No emoji, no AI attribution, subject ≤ 72 chars. On a shared `~/.claude` tree
with concurrent sessions, prefer the temp-index plumbing path
(`rules/concurrent-commit-plumbing.md`) — never fight shared HEAD.

### Step 5 — PR with a plain-language body

```bash
gh pr create -R <org>/<repo> --base main \
  --title "type(scope): summary" --body "<plain-English what + why, THEN how>"
```

The body is a **user-facing deliverable** — Rule 17 applies in full
(`rules/commit-standards.md` "Plain-language PR body"). Lead with "What this
does (plain English)" — WHY before HOW — before any path/flag/metric. The
in-session concision style does NOT license a compressed PR body. If the branch
already has a PR, skip creation and reuse its URL.

### Step 6 — AGORA review → verdict-gated convergence

Opening the PR **starts a loop**, never a passive wait
(`rules/ci-review-driven-convergence.md`). Poll CI + the AGORA verdict; branch
on the verdict (max 3–5 fix iterations):

| AGORA verdict | Action |
|---------------|--------|
| **MERGE** (PASS, at least two seats sitting, REQUEST_CHANGES from none of them) | Exit loop → Step 7. AGORA-green = ratified. |
| **DEFER_CHANGES** | AGORA auto-files the follow-up; surface one line → Step 7 (deferred ≠ blocking). |
| **REQUEST_CHANGES** | Stay in loop: R0 each concern against the *actual code* (verify-canonical, not the truncated-diff narrative) → fix CBC → push → re-run AGORA. |
| Judge unavailable / required check red / AGORA never ran | NOT a green. Fix or surface — never silent-merge (`rules/operational-principles.md` Rule 6). |

R0 discipline inside the loop: a true concern → fix root-cause; a false
positive → refute with a passing test, not an argument; an over-engineered
suggestion → reasoned, tested refusal (KISS/YAGNI still bind). Stuck > 30 min
or ≥ 3 iterations on verified-false / flaky-reviewer concerns with CI green and
no real danger → pragmatic admin-merge (`rules/operational-principles.md`
Rule 7) — but only after verify-canonical confirms the block is actually false.

### Step 7 — Merge (AGORA-green; bootstrap-class human-merged) + deploy + verify-canonical the deploy

**Merge gate** — the actor decides:

- **Interactive `/finish` (default)** → merge once AGORA is green: PASS/MERGE
  or DEFER_CHANGES, at least two seats sitting, no REQUEST_CHANGES from any
  sitting seat, required CI green, and the Codex hold in `bin/grip-pr-monitor`
  clear. AGORA-green is the exogenous anchor (`rules/exogenous-anchor-law.md`),
  and `CLAUDE.md` "AGORA-Green = Merge -- Don't Ask" applies to interactive
  sessions too (it superseded the old interactive human-merge default, V>>
  2026-05-30): merge, never ask "merge or hold?". The exception is a PR that
  creates or widens merge authority itself (bootstrap exception), or one
  `bin/grip-pr-monitor` holds as merge machinery whose effect changes merge
  decisions: drive it to green, then surface it and stop -- it is always
  human-merged (`rules/canonical-output-style.md`, the Authority class).
- **Explicitly authorised / autonomous-loop actor** → admin-merge per the
  RSI-loop carve-out (`gh pr merge <n> --repo <org>/<repo> --admin --squash`).

**After merge**: `git checkout main && git pull --rebase`. **Deploy** per the
target's path. Where the target deploys itself, let it run and never SSH for
it (`rules/operational-context.md`): GRIP's own relay host runs the
`scripts/relay-selfdeploy.sh` cron. An install without that script (a starter
kit has none) deploys the way the target documents. **Then verify-canonical
the deploy** — do NOT trust "deployed":

- `git show origin/main:<path> | head -5` — the byte landed.
- `curl -sf <url>/<route>` with a body assertion — the endpoint serves it.
- re-run the e2e — the full path works.

### Step 8 — Anti-drift close

Always emit the tight "What's Up Next" block (`rules/session-protocol.md`,
Rule 11) — even when the answer is "already done". Never skip it.

```text
Done: <terse — skip if the merge says it>
Remaining: <items not shipped this round>
Open: <PRs/merges still in flight, deferred follow-ups>
Next: <one concrete step>
```

---

## Anti-patterns (each is a logged falsifier)

| Anti-pattern | Why it is wrong |
|--------------|-----------------|
| Trusting a PR's "needs-merge" status without `gh pr view` | This session hit an already-merged PR and a duplicate "merge this" — Step 1 exists for exactly this. |
| Reimplementing the converge / ultrado / auto loop inline | DRY violation — this skill is an orchestrator; call the kernel. |
| Fanning out finish-off work to background agents reflexively | `rules/dispatch-economics.md`: < 3:1 useful-work-to-floor = do it at depth-0. |
| A compressed PR body | Rule 17: the PR body is user-facing, full plain English. |
| Asking "merge or hold?" on an AGORA-green PR, or self-merging a bootstrap-class PR | An AGORA-green PR merges without asking; only a bootstrap-class PR, or a merge-machinery PR that changes merge decisions, is human-merged. |
| Forcing a delight to justify the run | Step 3 is optional; a theatre-only delight is dropped (`loop-priority-external-over-theatre.md`). |
| Reporting "deployed" without probing the endpoint | verify-canonical the deploy — status ≠ live. |

---

## Knowledge-maturation tier + proving evidence

Per `rules/knowledge-maturation-functor.md`, this skill crystallises a pattern
that is **already N≥3 proven** — it is a rule-now/skill-now artifact, not a
speculative abstraction:

- This session ran the finish-off lifecycle end-to-end on four independent,
  verifiable instances — each "take in-flight work to DONE through verify →
  finish → seal → PR → review → merge", together clearing the N≥3 threshold:
  - **GRIP #3297** — context-gate sub-agent false-block fix (mesh-diagnosed →
    fix + validator UUID fix + protocol amendment → AGORA DEFER_CHANGES → merged).
  - **GRIP #3291** — channel-agnostic `/imagine`+`/emoji` (re-dispatched once the
    #3297 fix unblocked it → converged in 1 iteration → AGORA DEFER_CHANGES → merged).
  - **GRIP #3300** — `sanitizeRiscInput` test correction (verify-canonical proved a
    "security regression" was a test over-assertion → fixed → merged).
  - **hushbell #20** — ULTRAWIN MQTT state-machine + Ring Sigil delight (build →
    AGORA REQUEST_CHANGES on a real provenance bug → one-line fix + Goodhart anchor
    → merged → GitHub Pages deploy verify-canonical'd live).
  The kernels it composes are themselves already matured, so no new mechanical
  shim ships with this skill.
- The skill adds **no new enforcement** — it sequences matured kernels and
  rules. No mechanical shim ships with it (YAGNI): the gates it relies on
  (quality, no-stubs, ci-review-driven-convergence) already enforce
  mechanically at their own layers.

## Falsification

This skill is wrong if, over the next ~10 finish-off runs it governs:

- The verify-canonical Step 1 never once changes the run — every target's
  status already matched reality (then Step 1 is pure overhead → demote it to
  a one-line reflex). The two same-session counter-examples (already-merged PR,
  duplicate work) refute this *today*.
- Operators bypass the orchestrator and call the kernels directly more often
  than they use `/finish` (the sequencing adds no value → fold the checklist
  into `/go` or `/auto` and delete this skill).
- The verdict-gated loop produces churn without convergence on > 30% of runs
  (then the criterion is mis-set or AGORA is non-deterministic — fix the
  reviewer, per `ci-review-driven-convergence.md`, not this skill).
- An AGORA-green merge lets through a bad change that a human gate would have
  stopped (then tighten the gate for that class of PR -- narrow it, do not drop
  the lifecycle).

Track: finish-off runs reaching verified-DONE within ≤ 3 fix iterations
(target ≥ 80%); Step-1 reality-corrections per 10 runs (target > 0 — proves the
reflex earns its place); "deployed but not live" incidents (target 0).
Goodhart-safe: success is a *verified-live merged deliverable*, never the count
of `/finish` invocations.
