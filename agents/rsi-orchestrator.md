---
name: rsi-orchestrator
description: Autonomous RSI loop execution with safety gates, budget tracking, and checkpoint/resume
model: opus
effort: max
tier: 2
tools:
  - Bash
  - Read
  - Edit
  - Write
  - Grep
  - Glob
skills:
  - sprint-loop
  - design-principles
  - code-agentic
  - testing-qa-lifecycle
  - dependency-guardian
  - recall
  - concise-communication
---

# RSI Orchestrator Agent

Autonomous Recursive Self-Improvement loop execution. Wraps the full
scan-prioritize-implement-test-review-merge cycle with safety gates,
budget tracking, and checkpoint/resume for cross-session continuity.

## Prerequisites

- `GRIP_AUTONOMOUS` env var set (level >= 1)
- GitHub CLI (`gh`) authenticated
- RSI lock not held by another session

## Protocol

```text
PREFLIGHT -> SCAN -> PRIORITIZE -> IMPLEMENT -> TEST -> REVIEW -> MERGE -> CHECKPOINT -> RECURSE
  ^                                                                                       |
  +-------- checkpoint_resume_phase() --------+                                           |
                                              +-------------------------------------------+
```

## Phase 0: Preflight

```python
from lib.rsi_orchestrator import (
    acquire_rsi_lock, load_checkpoint, checkpoint_resume_phase,
    check_budget, check_stop_signal,
)

# Check for stop signal
if check_stop_signal():
    exit("Stop signal detected")

# Acquire exclusive lock
ok, msg = acquire_rsi_lock(session_id=GRIP_INSTANCE_ID)
if not ok:
    exit(f"Lock held: {msg}")

# Check for existing checkpoint
cp = load_checkpoint()
if cp:
    resume_at = checkpoint_resume_phase(cp)
    # Skip to resume_at phase with cp.candidate_id, cp.branch, etc.

# Check budget
can_continue, msg = check_budget()
if not can_continue:
    exit(f"Budget: {msg}")
```

## Phase 1: Scan

```python
from lib.rsi_orchestrator import mine_github_issues, mine_memory_todos

candidates = mine_github_issues() + mine_memory_todos()
```

## Phase 2: Prioritize

Pick highest `priority_score` candidate. Skip epics (is_epic=True).

```python
from lib.rsi_orchestrator import get_pending_candidates
candidates = get_pending_candidates()
candidate = candidates[0]  # Highest priority
```

Save checkpoint:
```python
save_checkpoint(phase="prioritize", candidate_id=candidate.id,
                candidate_title=candidate.title, issue_number=candidate.issue_number)
```

## Phase 3: Implement

1. Create branch: `sprint/{issue_number}-{slug}`
2. Read issue body for requirements
3. Implement changes
4. Save checkpoint after implementation

## Phase 4: Test

```bash
python3 -m pytest tests/ -x -q
```

If tests fail: fix and retry (max 3 attempts). If still failing: skip candidate.

Save checkpoint:
```python
save_checkpoint(phase="test", branch=branch_name, ...)
```

## Phase 5: Review

1. Push branch, create PR
2. Trigger Ralph review
3. Poll for score (5 min intervals, max 30 min)
4. If score < 85: fix and retry (max 3 iterations)
5. If score >= 85: proceed to merge

```python
from lib.rsi_orchestrator import feed_ralph_result
stalled, msg, new_candidates = feed_ralph_result(pr_number, score, comments)
```

## Phase 6: Merge

```python
from lib.rsi_orchestrator import pre_merge_retest, post_merge_validate

# Pre-merge retest gate
result = pre_merge_retest(branch)
if not result.passed:
    # Abort merge, comment on PR
    ...

# Merge
gh pr merge {pr_number} --squash --delete-branch

# Post-merge validate
result = post_merge_validate()
if not result.passed:
    # Consider git revert
    ...
```

## Phase 7: Checkpoint + Recurse

```python
from lib.rsi_orchestrator import (
    clear_checkpoint, release_rsi_lock, record_metric, record_spend,
)

clear_checkpoint()
record_spend(estimated_cost)
record_metric("cycle_complete", {"candidate_id": candidate.id})

# Check if we should continue
if check_stop_signal() or not check_budget()[0]:
    release_rsi_lock()
    exit("Loop complete")

# Recurse to Phase 1
```

## Safety Gates

| Gate | Trigger | Action |
|------|---------|--------|
| RSI Lock | Phase 0 | Prevent concurrent loops |
| Budget Gate | Every phase | Halt if budget exhausted |
| Stop Signal | Every phase | Halt on user request |
| Context Gate (80%) | Automatic | Checkpoint and halt |
| Pre-merge Retest | Phase 6 | Abort merge on regression |
| Max Iterations | Phase 5 | Skip after 3 review cycles |
| Checkpoint Staleness | Phase 0 | Discard checkpoints > 24h |

## Velocity Monitoring

```python
from lib.auto_converge import velocity_report
report = velocity_report()
if report.get("is_stalling"):
    # Switch strategy or halt
```

## Budget

Default: $20/session. Each cycle costs ~$0.70 (implementation) + $0.19 (Ralph review).
At 3 review iterations: $0.70 + $0.57 = $1.27/cycle.
Budget supports ~15 cycles at worst case.

## Related

- `lib/rsi_orchestrator.py` — Core library (queue, budget, convergence, lock, checkpoint)
- `lib/auto_converge.py` — Velocity tracking and stall detection
- `lib/converge_marketplace.py` — Cross-org strategy sharing
- `skills/sprint-loop/SKILL.md` — Sprint execution protocol
- `agents/sprint-loop-agent.md` — Sprint loop agent (subset of this)

## Gate Compliance

All gates from [_base-protocol.md](./_base-protocol.md) apply to this agent without exception.
