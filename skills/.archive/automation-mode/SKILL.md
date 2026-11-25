---
name: automation-mode-orchestration
description: Routes workflow automation through safety guardrails and recursive learning. Use when /mode automation is active.
status: Active
effort: high
version: 1.0.0
triggers:
  - /mode automation
  - automation mode active
integrates:
  - self-improvement-engine
  - recursive-learning
  - safety-guardrails
  - asking-users
---

# Automation Mode Orchestration

Routes autonomous operations through safety guardrails with recursive learning feedback. Orchestration layer only -- see `self-improvement-engine/SKILL.md` for the improvement cycle and `recursive-learning/SKILL.md` for pattern capture.

## Protocols

### 1. Safety Guardrails (Non-Negotiable)

Every autonomous operation must have:

| Guardrail | Requirement |
|-----------|-------------|
| Dual-condition exit | Completion signal AND explicit user confirmation |
| Circuit breakers | Stagnation detection, error threshold, output quality decline |
| Rate limiting | 100 API calls/hr, 50 file operations/min |
| Scope boundaries | Explicit allowed and forbidden actions |
| Rollback points | Checkpoint before every destructive change |

### 2. Autonomy Assessment

Before starting any automation:

```text
REVERSIBLE? --> YES --> Proceed with guardrails
     |
     NO
     |
     v
HALT --> AskUserQuestion with scope, risks, and rollback plan
```

### 3. Circuit Breaker Triggers

| Condition | Threshold | Action |
|-----------|-----------|--------|
| Stagnation | No progress for 5 iterations | HALT |
| Error rate | > 3 consecutive failures | HALT |
| Output decline | Quality score drops 20% | HALT |
| Rate limit | Approaching limits | Throttle |

### 4. RSI Integration

After each automation run:

1. Capture task outcomes
2. Extract patterns (3x threshold for YSH)
3. Feed learnings back to improve future runs

## Workflows

### Quick (single script, cron setup)

1. Autonomy assessment
2. Direct implementation with guardrails
3. Verify operation
4. Output

**Broly level**: THREAD (de-escalated)

### Standard (multi-step pipeline, batch processing)

1. Autonomy assessment
2. AskUserQuestion for scope confirmation
3. Implement with checkpoint gates
4. Circuit breaker monitoring
5. Completion confirmation
6. RSI pattern capture

**Broly level**: FORK (default)

### Deep (cross-repo automation, multi-stage orchestration)

1. Autonomy assessment via Broly CLUSTER:
   - Pipeline design agent
   - Safety verification agent
   - Execution monitoring agent
2. Parallel stage execution with checkpoints
3. Cross-stage coordination
4. Comprehensive RSI capture

**Broly level**: CLUSTER (escalated)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Starting any autonomous operation (scope confirmation)
- Circuit breaker triggers
- Stage transitions in multi-stage pipelines
- Completion confirmation required
- Rollback needed
- Rate limit approaching

## Output Style

- Status-first with progress indicators
- British English throughout
- No emoji
- Include timing and resource usage metrics
- Log format for automation output

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for automation mode |
