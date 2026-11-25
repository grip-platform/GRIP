---
name: autonomous-orchestration
description: Framework for orchestrating autonomous AI workflows through DEFINE, VALIDATE, EXECUTE, MONITOR, COMPLETE, LEARN with RSI feedback loops. Use when designing or running a long autonomous task pipeline.
---

# Autonomous Orchestration

Framework for orchestrating autonomous AI workflows with RSI feedback loops.

## Orchestration Model

### Task Lifecycle

```text
DEFINE → VALIDATE → EXECUTE → MONITOR → COMPLETE → LEARN
   ↑                                              │
   └──────────────────────────────────────────────┘
                    (RSI Loop)
```

### Phases

| Phase | Purpose | Safety Gate |
|-------|---------|-------------|
| **Define** | Specify task, scope, success criteria | Scope validation |
| **Validate** | Check feasibility, resources, permissions | Pre-flight check |
| **Execute** | Run task with monitoring | Circuit breakers |
| **Monitor** | Track progress, detect anomalies | Stagnation detection |
| **Complete** | Verify success, cleanup | Dual-condition exit |
| **Learn** | Feed outcomes to RSI | Pattern extraction |

## Task Definition

```markdown
## Autonomous Task: [ID]

### Goal
[One clear sentence describing desired outcome]

### Scope
**Allowed**:
- [Action 1]
- [Action 2]

**Forbidden**:
- [Action 1]
- [Action 2]

### Success Criteria
- [ ] [Criterion 1]
- [ ] [Criterion 2]

### Failure Conditions
- [Condition 1] → [Action]
- [Condition 2] → [Action]

### Timeout
[Duration] or [Operation count]

### Rollback
[How to undo if needed]
```

## Orchestration Patterns

### 1. Sequential Pipeline

```grip
pipeline⟿ {
    stages: [A, B, C, D]
    execution: sequential
    on_failure: stop ∨ skip ∨ retry(3)

    A.complete⟿ B.start
    B.complete⟿ C.start
    C.complete⟿ D.start
}
```

### 2. Parallel Fan-Out

```grip
parallel⟿ {
    stages: [A, B, C]
    execution: concurrent
    join: wait_all ∨ wait_any

    [A, B, C].start⟿ concurrent
    [A, B, C].complete⟿ join ⫶ next_stage
}
```

### 3. Conditional Branching

```grip
conditional⟿ {
    evaluate: condition
    branches:
        true: [A, B]
        false: [C, D]

    condition.eval⟿ select_branch ⫶ execute
}
```

### 4. Retry with Backoff

```grip
retry⟿ {
    attempts: 3
    backoff: exponential(base=2, max=60)
    on_exhaust: escalate

    attempt(1)⟿ execute
    failure⟿ wait(2^n) ⫶ attempt(n+1)
    attempts_exhausted⟿ AskUserQuestion
}
```

## RSI Integration

### Outcome Capture

After each autonomous task:

```grip
rsi.capture⟿ {
    task_id: [ID]
    outcome: [success | partial | failure]
    metrics: {
        duration: [time]
        operations: [count]
        errors: [list]
        rollbacks: [count]
    }
    learnings: [extracted patterns]
}
```

### Pattern Extraction

```grip
pattern.extract⟿ {
    if: same_error.count >= 3
    then: create_prevention_skill

    if: success_pattern.count >= 3
    then: create_acceleration_skill

    if: failure_mode.novel
    then: add_to_circuit_breaker
}
```

### Feedback Loop

```grip
rsi.loop⟿ {
    1. Capture task outcome
    2. Compare to predictions
    3. Identify deltas (better/worse than expected)
    4. Extract patterns from deltas
    5. Update orchestration rules
    6. Apply to future tasks
}
```

## Monitoring Dashboard

```markdown
## Task Monitor: [ID]

**Status**: [RUNNING | PAUSED | COMPLETE | FAILED]
**Progress**: [X/Y steps] (XX%)
**Duration**: [elapsed] / [timeout]

### Current Operation
[Description of what's happening now]

### Recent Events
| Time | Event | Status |
|------|-------|--------|
| [T1] | [Event] | [OK/WARN/ERR] |

### Health Indicators
- Progress: [healthy/stagnant]
- Errors: [count] (threshold: [N])
- Rate limit: [used/limit]
- Context: [used%]

### Actions
- [Pause] [Resume] [Abort]
- [Checkpoint] [Rollback]
```

## ut++ Integration

| Gate | Trigger | Action |
|------|---------|--------|
| Gate 4 | Orchestration affects production | Require explicit approval |
| Gate 5 | Multi-stage orchestration | Checkpoint between stages |

## AskUserQuestion Triggers

**MANDATORY** when:
- Starting orchestration
- Stage transition (if checkpointing)
- Anomaly detected
- Completion for confirmation

```text
Question: "Autonomous orchestration will execute [N] stages. Proceed?"
Header: "Orchestrate"
Options:
- "Execute all" - Run to completion
- "Step through" - Pause between stages
- "Dry run" - Simulate without changes
- "Abort" - Cancel orchestration
```

## Safety Integration

Orchestration must respect all safety-guardrails:

| Guardrail | Orchestration Application |
|-----------|---------------------------|
| Circuit breaker | Stop orchestration on stagnation |
| Rate limiting | Throttle parallel operations |
| Scope boundaries | Enforce per-stage |
| Rollback points | Create before each stage |
| Dual-condition exit | Verify success + explicit signal |

## Error Handling

### Error Categories

| Category | Response |
|----------|----------|
| Transient | Retry with backoff |
| Recoverable | Attempt fix, then continue |
| Fatal | Stop, rollback, report |
| Unknown | Pause, ask user |

### Escalation Path

```grip
escalate⟿ {
    1. Log error with context
    2. Attempt auto-recovery (if recoverable)
    3. If recovery fails: pause orchestration
    4. AskUserQuestion with options
    5. Based on response: retry | skip | abort | manual_fix
}
```

## Related Skills

- `safety-guardrails` - Protective mechanisms
- `self-improvement-engine` - RSI pipeline
- `recursive-learning` - Pattern learning
- `code-agentic` - Code operation safety

---


