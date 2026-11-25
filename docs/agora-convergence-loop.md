# AGORA Convergence Loop

The persistent /loop that drives CI Convergence for AGORA reviews on every open
PR across every enabled repo — "without fault" per V>>'s 2026-05-26 ask. The
runtime instance of `rules/ci-review-driven-convergence.md` operating at
**fleet scale**, not per-PR.

## What it does

Every ~15 minutes, the tick runs once. For each enabled repo it enumerates
open PRs and, for each PR, classifies the latest AGORA verdict and takes the
right action:

| Verdict          | Action                                      | Autonomous? |
|------------------|---------------------------------------------|-------------|
| `MERGE`          | `gh pr merge --admin --squash` if eligible  | YES (gated) |
| `REQUEST_CHANGES`| Log; surface for operator/RSI loop          | No (notify) |
| `DEFER_CHANGES`  | Log; AGORA itself self-creates the issue    | No (notify) |
| `FAIL` / `ERROR` | `gh workflow run agora-pr-review.yml` rerun | YES         |
| (none yet)       | Wait — until stale, then auto-rerun         | YES (gated) |

## Why AGORA PASS is enough to merge

Per `rules/exogenous-anchor-law.md` (V>>-ratified 2026-05-17, restated
2026-05-26): the cross-provider AGORA review (3 speakers + a fourth model as
LLM-as-judge, exogenous to the speakers) IS the exogenous anchor. The
producing model cannot control or please that verdict, so AGORA PASS
satisfies the "closed improvement loop must be evaluated by a process the
generating model neither controls nor can predict-to-please" law. The human
merge is *sufficient* but not *required*.

## Bootstrap exception (the non-negotiable kill switch)

A PR that modifies any of the convergence-loop's OWN files — the tick, the
verdict router, the repo config, or the governing rules — **MUST NOT** be
auto-merged by the tick. Otherwise the loop would be ratifying its own
authority: the self-fulfilling failure `exogenous-anchor-law.md` exists to
prevent. Enforced by `lib/agora_verdict_router.py::is_bootstrap_excepted()`
and tested in `tests/test_agora_verdict_router.py::TestBootstrapExcepted`.

The bootstrap-excepted paths are `BOOTSTRAP_EXCEPTED_PATHS` in
`lib/agora_verdict_router.py`, matched as prefixes from the repo root. They
cover the loop's own files, the gates that govern autonomous merge (AGORA's
verdict code, the CI workflows, `bin/grip-pr-monitor`, `bin/grip-merge-when-green`
and the guardrail-removal merge gate), and the documents that
grant the merge authority (the governing rules, both copies of `CLAUDE.md`, and
`AGENTS.md`).
The list is kept in that one place on purpose: this page used to copy it and
fell behind (GRIP#7927). The merge sweep (`bin/grip-pr-monitor`) and AGORA's
ladder hold the same list, and `tests/test_grip_pr_monitor.py` checks that.

If you change any of these, the tick will tell you so in telemetry and stop —
go merge it yourself after a human eye on the change.

## Architecture (syscall-doctrine compliant)

```
[cron / CronCreate every 15 min]
        ↓
[bin/agora-convergence-tick]   ← bash wrapper, does ALL gh I/O
        ↓ (for each enabled repo, each open PR)
   gh api repos/<o>/<r>/pulls?state=open
   gh api repos/<o>/<r>/issues/<n>/comments
   gh pr checks <n> --json state,workflow,name
   gh api repos/<o>/<r>/pulls/<n>/files
        ↓ (data fed in as args/stdin to)
[lib/agora_verdict_router.py]   ← pure functions, no I/O, no subprocess
   latest_agora_verdict(comments)
   route_action(verdict)
   should_rerun_stale(pr_opened_at, now_iso, latest_agora, stale_after_minutes)
   auto_merge_eligible(verdict, ci_checks_green, changed_paths)
   is_bootstrap_excepted(changed_paths)
        ↓ (returns action label + eligibility)
[tick takes action]
   gh workflow run … (rerun on FAIL/stale)
   gh pr merge --admin --squash (auto_merge if eligible)
   log to data/agora-convergence-tick.jsonl (every PR)
```

The pure-function core is the syscall-doctrine boundary: tests cover every
branch with no gh/subprocess mocks because there is no I/O to mock. The bash
wrapper does the I/O and is tested by smoke-running it with `AGORA_TICK_DRY_RUN=1`.

## Operate

```bash
# One-shot tick against all enabled repos
bin/agora-convergence-tick

# Tick against one specific repo
bin/agora-convergence-tick grip-platform/GRIP

# Decide-but-do-not-act (log decisions only)
AGORA_TICK_DRY_RUN=1 bin/agora-convergence-tick

# Inspect last 10 tick decisions
tail -10 data/agora-convergence-tick.jsonl | jq .
```

## Schedule (true /loop)

The tick is invoked on a cron schedule. Three options:

### Option A — Claude Code native CronCreate (recommended)

Inside any Claude session:

> Schedule the AGORA convergence tick every 15 minutes (`*/13 * * * *`),
> durable so it survives session restarts, with the prompt:
>
>     bash ~/.claude/bin/agora-convergence-tick && tail -1 ~/.claude/data/agora-convergence-tick.jsonl
>
> Make the cron tag the result with `[agora-tick]` so I can spot it in scrollback.

The tick is idempotent — duplicate invocations don't cause double-merges
because `gh pr merge` on an already-merged PR is a no-op error that the tick
absorbs into telemetry.

### Option B — launchd (macOS, full laptop-independence)

Drop a launchd plist at `~/Library/LaunchAgents/com.codetonight.agora-tick.plist`
that runs `bin/agora-convergence-tick` every 15 min. See `skills/launchd-automation`.

### Option C — GitHub Actions cron (full org-independence)

Run from GitHub Actions infra — no laptop needed at all. Workflow scaffold
deferred to a follow-up issue once the local tick has proven its action
matrix in production.

## Telemetry

`data/agora-convergence-tick.jsonl` accumulates one line per PR per tick:

```json
{"ts":"2026-05-26T01:23:04Z","repo":"grip-platform/GRIP","pr":3249,"branch":"feat/intel-skill-modes-w0","verdict":"","action":"wait","reason":"agora_pending","dry_run":1}
```

Useful queries:

```bash
# Action distribution over the last 1000 ticks
jq -r '.action' data/agora-convergence-tick.jsonl | tail -1000 | sort | uniq -c | sort -rn

# Recent auto-merges (the loop ratifying AGORA PASS)
jq -c 'select(.reason=="auto_merged_admin_squash")' data/agora-convergence-tick.jsonl | tail -10

# Recent reruns (recovery from hallucination-fail / staleness)
jq -c 'select(.action=="rerun")' data/agora-convergence-tick.jsonl | tail -10

# Recent blocked-merges (bootstrap-excepted, awaiting human review)
jq -c 'select(.reason | startswith("merge_verdict_blocked"))' data/agora-convergence-tick.jsonl | tail -10
```

## Configure enabled repos

Copy the example config to a local copy (gitignored), edit, save:

```bash
cp state/agora-convergence-repos.example.json state/agora-convergence-repos.json
$EDITOR state/agora-convergence-repos.json   # set enabled:true on each repo you want included
```

The tick prefers the local `.json` over the `.example.json` fallback.

## Falsification

The convergence loop is wrong if, over ~30 days of operation:

- `auto_merged_admin_squash` ever fires on a bootstrap-excepted PR (the
  kill-switch failed) — revert and add the missing path to
  `BOOTSTRAP_EXCEPTED_PATHS` immediately.
- The `rerun` action ever fires on a PR that already had a valid AGORA
  verdict — verdict parser's staleness logic is wrong.
- The MERGE auto-merge produces measurably worse outcomes than human merge
  over comparable PRs (regression rate, post-merge revert rate) — relax
  to "merge_ready, surface for V>> merge" per the prior contract.
- Operator overhead to maintain the loop exceeds the operator time it saves
  (the loop is friction, not lever) — kill it.

Track per tick: per-action counts, per-repo counts, eligibility-reason
breakdown for blocked merges. Goodhart-safe: "success" is PR-shipped-without-
regression, not auto-merge invocation count.
