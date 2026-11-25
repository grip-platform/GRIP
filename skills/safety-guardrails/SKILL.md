---
name: safety-guardrails
description: Safety protocols for autonomous and automated operations. Use when configuring an autonomous workflow, setting up CI/CD safety gates, or deciding what an unattended loop may not do.
---

# Safety Guardrails

Safety protocols for autonomous and automated operations.

## When to Use

- Configuring autonomous workflows
- Setting up CI/CD safety gates
- Defining operational boundaries
- Automation-mode sessions

## Guardrail Types

### Hard Guardrails (Never Override)

| Guardrail | Description |
|-----------|-------------|
| No secret exposure | Never log/display secrets |
| No destructive actions without confirmation | Delete, force push, drop table |
| No unbounded loops | Max iterations on all loops |
| No untested deployments | Tests must pass before deploy |

### Soft Guardrails (Override with Justification)

| Guardrail | Description | Override Condition |
|-----------|-------------|-------------------|
| PR required for main | Direct push blocked | Hotfix with approval |
| Code review required | Merge blocked | Solo developer + tests pass |
| Max file changes per commit | Warning at threshold | Refactoring with plan |

## Implementation

### Operation Limits

```yaml
limits:
  max_files_per_commit: 20
  max_operations_per_session: 100
  max_retries_per_action: 3
  context_gate_threshold: 0.85
```

### Circuit Breakers

| Breaker | Trigger | Action |
|---------|---------|--------|
| Error rate | 3 failures in 5 minutes | Pause, report |
| Token usage | 85% of budget | Checkpoint, warn |
| Time elapsed | 2 hours continuous | Break, summarize |

### Rollback Protocol

1. Before any destructive action: create checkpoint
2. After action: verify state matches expectation
3. On failure: rollback to checkpoint
4. Report: what happened, what was rolled back

## Verification

```bash
# Check all guardrails active
python3 lib/auto_converge.py check

# Verify safety gates
python3 lib/verify_hooks.py
```

## Integration

Used by: automation mode. See also: context-gate, autonomous-learning.
