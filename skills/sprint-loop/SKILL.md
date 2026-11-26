---
name: sprint-loop
description: Self-healing autonomous sprint with guardrails. Scans GitHub issues, implements, scores locally (MoE), creates PR, gates via CI + Ralph, auto-fixes, merges. Saves state on context pressure for session handoff.
effort: high
status: Active
version: 2.0.0
triggers:
  - "sprint loop"
  - "autonomous sprint"
  - "pick up issues"
  - "sprint-loop issues"
  - "rsi sprint"
integrates:
  - autonomous-learning
  - ralph-wiggum-loop
  - pr-automation
  - kono-memory
  - llama-reviewer
  - pre-review-pipeline
---

# Sprint Loop v2 — Self-Healing RSI Sprint

Autonomous issue execution with explicit checkpoints, local MoE pre-scoring,
multi-tier review gating, and session handoff on context pressure.

**Plain English summary**: an autonomous loop that picks up GitHub
issues labelled "sprint-loop", implements them one by one, gets the
work scored locally by a small AI model first (free), then by a more
expensive cloud reviewer (Ralph) only if the local score is good
enough. If something fails, the loop tries to fix itself up to three
times before tagging a human. Saves its state when the context window
fills up so a fresh session can continue where it left off.

**Glossary**:
- RSI sprint = improvement cycle session
- MoE = Mixture of Experts (local AI scoring with multiple specialised models)
- Llama 3.2 3B = small local AI model used for free scoring
- Ralph = cloud-based authoritative reviewer (Anthropic Claude as judge)
- CI = continuous integration (automated tests on every push)
- Worktree = isolated working copy of the repository
- Goodhart violation = the loop chasing its own metrics instead of doing real work
- Token budget = how much context the loop can use per issue
- Self-healing = the loop fixes its own problems instead of stopping

## Prerequisites

- `GRIP_AUTONOMOUS=1` OR `~/.claude/.autonomous-state.json` with level >= 1
- GitHub CLI (`gh`) authenticated
- `sprint-loop` labeled issues in the repository
- For local scoring: `~/.claude/venv/bin/python3` with `llama_cpp_python`

## Protocol

```text
SCAN -> PRIORITIZE+CLAIM -> EXECUTE -> LOCAL-SCORE -> PR+CI -> RALPH -> GATE -> CLOSE+RELEASE -> LOG -> RECURSE
  ^                            |            |        |                                |
  |                      fix <-+     fix <--+  fix <-+                                |
  +--[context pressure: SAVE STATE + CONTINUATION_PROMPT.md]--+                       |
  +-------------------------------------------------------------------<---------------+
```

## Claim Before Work (PARAMOUNT)

PRIORITIZE does not mean "take the top of the backlog". Every concurrent worker
sorts the same list the same way and would pick the same item — which is exactly
what happened on 2026-08-10, when GRIP #4411 drew three independent PRs in twenty
minutes and #4443 drew four in sixteen (issues #4489, #4501).

Ask the claim gate instead:

```bash
gh issue list --label sprint-loop --state open --json number,title,labels --limit 20 \
  | python3 ~/.claude/lib/sprint_loop_claim.py pick "$CLAUDE_CODE_SESSION_ID"
# exit 0 -> the printed issue is yours (leased). exit 1 -> nothing free; stop.

python3 ~/.claude/lib/sprint_loop_claim.py release {number} "$CLAUDE_CODE_SESSION_ID"
```

It returns the first issue no live worker holds and no open PR already covers,
taking an atomic lease (`lib/task_claims.py`) before handing it back. The lease
expires after 4 hours, and early if the holding session dies, so a crashed worker
never blocks an issue. Protocol: `rules/task-lease-claim-before-work.md`.

The lease file is local, so it is true mutual exclusion between workers on ONE
machine — where both incidents occurred. The open-PR check is the only
cross-machine signal and is advisory, not a lock.

## Review Lifecycle (Cost Cascade)

```text
1. LOCAL MoE (free)    Llama 3.2 3B, 5 experts, generalist scoring
2. PR + CI (free)      GitHub Actions pre-review (analyze-pr.py)
3. RALPH (2-5 cents)   Anthropic Claude LLM-as-Judge, authoritative
4. AUTO-FIX (local)    Parse Ralph feedback, fix, re-score locally
5. RE-SUBMIT           Push fixes, re-trigger Ralph (max 3 attempts)
6. MERGE or ESCALATE   >= 80: squash merge. 3 failures: human tag
```

## Key Guardrails

| Gate | Rule |
|------|------|
| Issue claim | Only work an issue `sprint_loop_claim.py pick` handed you (PARAMOUNT) |
| Worktree isolation | All work in `/tmp/sprint-{N}` (PARAMOUNT) |
| 3-attempt revert | Tests, CI, Ralph each get 3 fix attempts |
| Context pressure | Save state + CONTINUATION_PROMPT.md at 70% |
| Token budget | 50K per issue, WIP commit if exceeded |
| Protected branches | Never push directly to main |

## Artefacts

| File | Purpose |
|------|---------|
| `SPRINT_LOG.md` | Per-issue log: branch, PR, scores, status |
| `CONTINUATION_PROMPT.md` | Session handoff for context pressure |

## Anti-Drift Output (Rule 11)

After each issue closes (CLOSE phase) and after each session-handoff
checkpoint, emit the Rule 11 tight 4-line template:

```
Done: <issue # + outcome — skip when commit/PR subject says it>
Remaining: <issues still in queue>
Open: <PRs in flight, Ralph reviews pending>
Next: <next issue or fresh session if context pressure hit>
```

Drop the "Done" line when the merged PR subject already states the
outcome — that's signal-to-noise hygiene. "Remaining/Open/Next" are
always-on so the operator can pivot the sprint informedly.

## Goodhart Self-Healing Gate

At the start of each sprint iteration (before SCAN phase):

1. Run `pre_flight_check()` from `lib/goodhart_guard.py`
2. IF `(False, reason)` — do NOT halt the sprint. Self-heal:
   a. **REVIEW**: Parse which check(s) are critical
   b. **SCHEDULE FIX**: Create corrective PR based on violation type:
      - `self_referential` -> pick user-facing issue from backlog
      - `pr_size` -> batch next 2-3 changes into one PR
      - `meta_work_cap` -> pick feature issue, skip infra
      - `complexity_ceiling` -> run `/prune-branches` or `/prune-skills`
   c. **SCORE**: Re-run `pre_flight_check()` after fix merges
   d. IF passes -> continue sprint
   e. IF fails -> back to (b), max 2 retries then AskUserQuestion
3. IF `(True, "")` with warnings: log, continue
4. IF `(True, "")`: continue silently

**Design rationale**: RSI/sprint-loop must NEVER halt. A Goodhart violation is
a task to fix, not a reason to stop. Same self-healing pattern as Ralph auto-fix.

## Detailed Execution Protocol

See `skills/sprint-loop/RUNBOOK.md` for the full 10-phase execution
protocol with checkpoints, bash commands, and error handling.

## Related

- `ralph-wiggum-loop` — PR review automation
- `autonomous-learning` — Task lifecycle framework
- `pr-automation` — PR creation workflow
- `kono-memory` — Episodic memory storage
- `llama-reviewer` — Local MoE scoring engine (generalist)
- `pre-review-pipeline` — L0 heuristic + L1 scoring + L2 council
