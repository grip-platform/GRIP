---
name: ultrawin
description: Capstone autonomous-delivery meta-skill — fires GRIP's full power (recon → parallel implement/verify/AGORA-merge → specialist fleet → auto-deploy → report) to take a goal or the safe-auto backlog from intent to verified-merged wins, with maximum safe parallelism and zero theatre. Thin orchestrator — composes existing kernels, reimplements none.
status: Active
effort: high
version: 1.0.0
triggers:
  - /ultrawin
  - "ultrawin"
see_also:
  - auto
  - converge
  - finish
  - loop-engineering
---

# /ultrawin — the capstone autonomous-delivery meta-skill

**Plain English**: you point `/ultrawin` at a goal (or let it default to the open
safe-auto backlog). It verifies what is *actually* true, classifies the work into
safe-to-automate vs hold-for-a-human, then fans out a fleet of agents to implement
each safe item, adversarially verify it, and merge it **only** once the
cross-provider AGORA council + CI both agree — while a parallel specialist fleet
audits, learns, plans, and verifies. Merges self-deploy. Risky work is held for
you, never reckless-merged. It reports what shipped, what is held, and the token
cost.

`/ultrawin` is a **thin orchestrator** (the `/finish` pattern): it **composes
existing kernels and reimplements nothing**. It is the exact orchestration this
repo has run repeatedly — now named, gated, and made repeatable.

## What it does NOT do (DRY — composes, never duplicates)

Every heavy behaviour is imported as-is. `/ultrawin` only sequences these and adds
the verify-canonical reflexes at the seams.

| Phase | Imported kernel (do NOT restate its logic) |
|---|---|
| Least-token discipline | `skills/efficient/SKILL.md` + `rules/dispatch-economics.md` (depth-0 bias; fan out only when useful_work ≥ 3× the agent floor) |
| Real-state verification | `rules/verify-canonical-not-claimed.md` + `skills/finish/SKILL.md` Step 1 |
| Recon / classify | the `converge(state, criterion, improve)` kernel — `rules/loop-engineering.md` / `lib/converge.py` |
| Per-item delivery | `skills/auto/SKILL.md` Shape A + the Workflow fan-out pattern (pipeline implement → verify → merge) |
| Novel delight++ | `skills/converge/SKILL.md` under a CBC + anti-theatre criterion |
| PR convergence | `rules/ci-review-driven-convergence.md` (poll → R0 each concern → fix → re-poll, verdict-gated) |
| Merge anchor | `rules/exogenous-anchor-law.md` (CI-green + AGORA-PASS the only anchor) + AGORA (`lib/agora_pr_review.py`) |
| Specialist fleet | the named agents (`agents/*.md`): hal-gateway-startup, learning-agent, learning-janitor, broly, Plan, the `*-enforcer` reviewers, sprint-loop-agent |
| Isolation | `lib/grip-worktree.sh` (git-crypt-safe worktrees) + `rules/concurrent-commit-plumbing.md` |
| Deploy | the target's own path: on GRIP's relay host, the automatic `scripts/relay-selfdeploy.sh` cron (never SSH); an install without that script (a starter kit) deploys as the target documents |

If a step seems to need new logic, you are doing it wrong — find the kernel.

## The lifecycle

```text
- [ ] 1 SCOPE   — verify-canonical the real state; run the dispatch-economics gate (depth-0 vs fan-out)
- [ ] 2 RECON   — classify the backlog: SAFE_AUTO vs HOLD (converge kernel)
- [ ] 3 EXECUTE — Workflow fan-out: per item implement (CBC, worktree) → multi-lens adversarial Goodhart-verify → AGORA-converge → merge-on-green
- [ ] 4 FLEET   — specialist agents in parallel (HAL-verify, learning-mine, janitor, broly council, plan-the-HOLDs, quality audits, subsystem audits)
- [ ] 5 DELIGHT++ — optionally /converge ONE external-facing delight under a CBC + anti-theatre criterion (skip if nothing external changes)
- [ ] 6 DEPLOY  — merges auto-deploy where a deploy cron is installed (GRIP's relay host: the 3-min `scripts/relay-selfdeploy.sh` cron), elsewhere deploy as the target documents; verify-canonical the deploy
- [ ] 7 REPORT  — predicted-vs-actual tokens, merged/held/learnings, anti-drift
```

## Native fleet substrate (CC 2.1.154 → 2.1.197)

The fan-out (step 3) and specialist fleet (step 4) ride native Claude Code
primitives, adopted as infrastructure — GRIP supplies the governance:

- **Workflow tool** (dynamic workflows, CC 2.1.154) — the deterministic
  orchestration primitive for the per-item pipeline (implement → verify → AGORA →
  merge). Tens-to-hundreds of background agents; `/workflows` to watch.
- **Agent teams** (CC 2.1.178, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) — spawn
  teammates directly via the Agent tool's `name` parameter; every session has one
  implicit team.
- **5-deep subagents** (CC 2.1.172) + **subagent permission prompts surface in the
  main session** (CC 2.1.186) — the fleet's gated tool calls are no longer silently
  auto-denied; the depth-0 main session answers.

**Pool discipline (the load-bearing constraint).** Native fan-out runs on **Pool A**
(the Anthropic harness — weekly-limited; it IS the `/usage` %). Per
`pool-a-offload-routing.md`, `/ultrawin` keeps the *structure* on the Workflow tool
but routes the LLM *cognition* — councils, deliberation, review legs — through **HAL
Pool B** (`grip-council --roster open`, `grip-sovereign-council`,
`lib/broly_council.py`), so a large fleet does not torch the weekly limit. The
AGORA/CI merge anchor stays exogenous and non-Anthropic.

## Usage

```bash
/ultrawin                  # default: sweep the open SAFE_AUTO backlog
/ultrawin "<goal>"         # scope to a goal
```

Requires ULTRACODE opt-in (the Workflow tool); for unattended runs, grip-yolo +
operator ratification. Without those, `/ultrawin` degrades to a single-item
`/finish` at depth-0.

## Safety envelope (hard — yolo NEVER relaxes these)

- Isolated git-crypt worktrees per item (`lib/grip-worktree.sh`); never edit the
  shared main tree; never touch `settings.json` under a concurrent session.
- **CI-green + AGORA-PASS is the ONLY merge anchor** (`exogenous-anchor-law.md`) —
  never a self-score.
- Hard no-list: no `settings.json` / safety-gate / merge-authority / bootstrap
  changes; no force-push; no destructive ops; no channel/WhatsApp posts.
- **Risky / behaviour-changing items are HELD for human review, never auto-merged
  unattended.**
- Never bypass or obfuscate around any gate (`no-guardrail-evasion.md`) — fix the
  root cause.
- A transient 429 ("not your usage limit") is a back-off-and-retry relief valve,
  never a loop-stop (`rate-limit-never-blocks.md`).

## Anti-theatre gate (the thing that makes it a WIN, not a show)

Per `rules/loop-priority-external-over-theatre.md`: bias to EXTERNAL value; no
speculative work that gets abandoned. **Success is measured by verified-merged
wins and external signal — NEVER by agent count, commit count, or "it feels
powerful."** Before each item ask: *if this merges, what changes for someone
outside the building?* If the honest answer is "nothing — it makes GRIP feel more
capable," it is theatre-until-proven; do the external thing first.

## Knowledge-maturation tier (N≥3 proven, rule-now)

Per `rules/knowledge-maturation-functor.md`, `/ultrawin` crystallises a pattern
already run ≥3× in one session (dinner sweep, overnight sweep, agent fleet). It
ships **no new heavy logic** — it sequences matured kernels, so no mechanical shim
ships with it (the gates it relies on already enforce at their own layers).

## Falsification

`/ultrawin` is wrong if, over the next ~10 runs: it over-fans-out on single-file
work (floor-waste — `dispatch-economics` violated; do it depth-0); OR it ever
auto-merges a risky/behaviour-changing item unattended (the HOLD gate failed — fix
it, never relax it); OR its "wins" are internal machinery with no external signal
(theatre — `loop-priority` violated); OR operators reach for the raw kernels
(`/auto`, `/converge`) more than `/ultrawin` (the sequencing adds no value — fold
it into `/auto` and delete this skill). Track: verified-merged-external-wins per
run; risky-auto-merge incidents (target 0); floor-waste dispatches (target 0).

## Anti-patterns

| Anti-pattern | Why wrong |
|---|---|
| Reimplementing the converge/auto/Workflow loop inline | DRY — call the kernel |
| Fanning out single-file work | `dispatch-economics`: < 3:1 useful:floor = depth-0 |
| Auto-merging a risky item to "be thorough" | The HOLD gate is non-overridable |
| Counting agents/commits as success | Anti-theatre: only external-verified wins count |
| Treating a transient 429 as a stop | `rate-limit-never-blocks`: back off + retry |
