# Sprint Loop v2 — Execution Runbook

Full 10-phase protocol with checkpoints, commands, and error handling.
See `SKILL.md` for the summary.

## External Tools (GRIP Framework)

This runbook references tools provided by the GRIP framework:
- `lib/grip-worktree.sh` — git worktree management with git-crypt support
- `lib/ralph_poller.py` — polls Ralph Wiggum GHA for PR scores with retry
- `lib/kono_substrate.py` — episodic memory storage (KONO subsystem)
- `lib/pre_review_pipeline.py` — L0 heuristic + L1 scoring + L2 council
- `lib/llama_reviewer.py` — local Llama MoE generalist reviewer

## Phase 1: SCAN

```bash
gh issue list --label sprint-loop --state open --json number,title,body,labels
```

If no issues: write "Sprint complete" to SPRINT_LOG.md, exit 0.

**Checkpoint 1**: Backlog loaded, issues parsed.

## Phase 2: PRIORITIZE

| Priority | Label | Order |
|----------|-------|-------|
| Critical | `P0` | 1st |
| High | `P1` | 2nd |
| Medium | `P2` | 3rd |
| Default | (none) | 4th |

Pick highest-priority, one at a time.

**Checkpoint 2**: Issue selected, logged to SPRINT_LOG.md.
If unclear requirements: comment, label `sprint-loop-blocked`, skip.

## Phase 3: EXECUTE

```bash
# Worktree isolation (PARAMOUNT)
bash ~/.claude/lib/grip-worktree.sh add /tmp/sprint-{number} sprint/{number}-{slug}
cd /tmp/sprint-{number}
# ... implement ...
# ... run tests ...
git commit -m "feat: {description}

Closes #{number}"
```

### 3-Attempt Revert Policy

1. **Attempt 1**: Fix the failing tests directly
2. **Attempt 2**: Revise implementation approach
3. **Attempt 3**: `git restore .` to revert ALL changes

After 3 failures: comment on issue, label `sprint-loop-blocked`, log blocker, skip.

### Token Budget (50K per issue)

If exceeded: commit with `[WIP]`, push (no PR), log partial progress,
create continuation issue with `sprint-loop` label + same priority.

**Checkpoint 3**: Tests green, commit made, SPRINT_LOG.md updated.

## Phase 4: LOCAL-SCORE (MoE Pre-Screen)

```bash
git diff main...HEAD > /tmp/sprint-{number}.diff
~/.claude/venv/bin/python3 ~/.claude/lib/pre_review_pipeline.py \
  --diff-file /tmp/sprint-{number}.diff \
  --pr-number {number} --pr-title "{title}" \
  --pr-author "$(git config user.name)" --local --verbose
```

| Score | Action |
|-------|--------|
| >= 75 | Proceed to PR |
| 65-74 | Fix weakest dimension, re-score |
| < 65 | Comprehensive fix, re-score |
| < 65 after fix | Proceed anyway (3B model limitation) |

**Checkpoint 4**: Score + dimensions logged.

## Phase 5: PR+CI

```bash
git push -u origin sprint/{number}-{slug}
gh pr create --title "{issue title}" --body "$(cat <<'EOF'
## Summary
Implements #{number}.

## Local MoE Score
| Dim | Score |
|-----|-------|
| Quality | {q}/100 |
| Alignment | {a}/100 |
| Testing | {t}/100 |
| Docs | {d}/100 |
| **Composite** | **{c}/100** |

## Test plan
- [ ] All existing tests pass
- [ ] New tests cover changes
- [ ] CI pre-review passes
EOF
)"
```

Wait for CI: `gh pr checks {pr-number} --watch --fail-fast`

| CI Result | Action |
|-----------|--------|
| Pass | Proceed to Ralph |
| Fail | Fix, push, re-run (max 3) |
| 3 failures | Label `sprint-loop-needs-human` |

**Checkpoint 5**: PR created, CI status logged.

## Phase 6: RALPH

Ralph reviews via GHA webhook. Poll:

```bash
python3 ~/.claude/lib/ralph_poller.py --pr-number {pr-number} --timeout 300
```

## Phase 7: GATE

| Ralph Score | Action |
|-------------|--------|
| >= 80 | Proceed to CLOSE |
| < 80 | Fix, push (append, never force-push), re-request |
| 3 failures | Label `sprint-loop-needs-human` |

### Auto-Fix Loop

1. Parse Ralph feedback for weakest dimension
2. Fix in worktree, run tests
3. Score locally (must improve or hold)
4. Commit: `fix: address Ralph feedback on {dimension}`
5. Push, re-request: `gh pr comment {pr} --body "@ralph please re-review"`

**Checkpoint 7**: Ralph score + fix attempts logged.

## Phase 8: CLOSE

1. `gh pr merge {pr-number} --squash --delete-branch`
2. Verify issue closed (or `gh issue close {number}`)
3. Clean worktree: `bash ~/.claude/lib/grip-worktree.sh remove /tmp/sprint-{number}`
4. KONO memory: `python3 ~/.claude/lib/kono_substrate.py store --domain sprint-loop --key "issue-{number}" --content "..."`

**Checkpoint 8**: Merge confirmed, KONO stored.

## Phase 9: LOG

Append to `SPRINT_LOG.md`:

```markdown
## Issue #{number}: {title}

| Field | Value |
|-------|-------|
| Branch | `sprint/{number}-{slug}` |
| PR | #{pr-number} |
| Files changed | {count} |
| Tests added | {count} |
| Local MoE | {score}/100 |
| Ralph | {score}/100 |
| Fix attempts | {count} |
| Status | {merged|blocked|skipped} |
```

## Phase 9.5: CLOSEOUT (Gen 487)

Run the RSI closeout chain to capture learning from this cycle:

```bash
python3 -c "
import sys; sys.path.insert(0, '$HOME/.claude/lib')
from rsi.checkpoint import run_closeout
results = run_closeout(candidate_id='sprint-{number}')
for r in results:
    status = 'OK' if r.success else 'FAIL'
    print(f'  [{status}] {r.name}: {r.elapsed_sec:.1f}s')
"
```

Steps (error-isolated — failure of one does not block others):
1. `/evo` — Calibrate friction metrics from this cycle
2. `/learn` — Consolidate insights into rules
3. `/save` — Persist state for cross-session continuity

**Checkpoint 9.5**: Closeout chain logged to RSI metrics.

## Phase 10: RECURSE

Loop to Phase 1. Stop when:
- No `sprint-loop` issues remain
- Token budget exhausted (context gate)
- Agent cancellation signal

## Context Pressure Protocol

At 70% context or after 5 items:

1. Commit in-progress work (`[WIP]`)
2. Push branch
3. Save state to SPRINT_LOG.md
4. Generate CONTINUATION_PROMPT.md:

```markdown
# Sprint Continuation Prompt

## Status
- **Completed**: {N} issues ({list})
- **In progress**: #{current} on branch sprint/{number}-{slug}
- **Remaining**: {N} issues ({list})

## Resume
1. Read SPRINT_LOG.md
2. Check branch sprint/{number}-{slug}
3. Resume from Phase {N}
4. Remaining backlog: [list]

## Command
/rsi sprint-loop
```

5. Exit cleanly.
