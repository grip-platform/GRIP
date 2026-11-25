---
name: full-converge
description: Recursive convergence at every decomposition level. Epic > task > sub-task > atom, each with criterion-based exit gates. The fractal application of converge() across project hierarchies.
status: Active
effort: high
version: 1.0.0
triggers:
  - "/full-converge"
  - "recursive converge"
  - "fractal convergence"
  - "converge at every level"
integrates:
  - converge.py
  - converge_combinators.py
  - converge_dna.py
  - converge_metrics.py
see_also:
  - converge
  - design-principles
  - principles-criterion
  - broly-meta-agent
---

# /full-converge -- Recursive Epic Convergence

The pattern: apply `converge(state, criterion, improve)` at EVERY level of decomposition, not just the leaf tasks.

"Fractal" in this skill means exactly that: the same three-part loop at each of the four named levels (epic, task, sub-task and atom). It is a pattern a session follows, not nesting that code performs for it. The deepest nesting a GRIP test exercises is three levels (`tests/test_convergence_integration.py`, "converge(converge(converge(state))) — 3 levels deep"), and Claude Code subagents cannot start subagents of their own (`agents/CLAUDE.md`).

## The Architecture

```
converge(EPIC, epic_criterion, epic_improve)
  |
  +-- converge(TASK_1, task_criterion, task_improve)
  |     |
  |     +-- converge(SUB_TASK_1a, sub_criterion, sub_improve)
  |     +-- converge(SUB_TASK_1b, sub_criterion, sub_improve)
  |
  +-- converge(TASK_2, task_criterion, task_improve)
        |
        +-- converge(ATOM_2a, atom_criterion, atom_improve)
```

Each level has its own:
- **State**: the current quality of that unit of work
- **Criterion**: what "done" means at that level (tests pass, score >= 90, all sub-tasks converged)
- **Improve**: the transformation applied at that level

## Why This Works

The convergence kernel is **substrate-independent** -- it does not care what `state` is. This means the same `converge()` function that improves a single function also improves an entire epic, because:

1. **Self-similarity**: An epic converges when all its tasks converge. A task converges when all its sub-tasks converge. This is recursive by nature.
2. **Criterion composition**: `all_of(task_1_done, task_2_done, task_3_done)` is itself a valid criterion for the epic level.
3. **Graceful degradation**: If a sub-task hits `max_depth` without satisfying, the parent level can switch strategy (reassign, simplify, skip).

## Protocol

### Step 1: Decompose

Break the work into a hierarchy:

```
Epic: "Improve convergence architecture"
  Task 1: "Wire Broly pipeline connections"
    Sub-task 1a: "Wire consolidator -> KONO"
    Sub-task 1b: "Wire orchestrator -> delivery report"
  Task 2: "Create /full-converge skill"
  Task 3: "Codify pattern as principle"
```

### Step 2: Define Criteria at Each Level

```python
# Atom level: specific, testable
atom_criterion = lambda s: s["tests_pass"] and s["lint_clean"]

# Task level: all atoms converged
task_criterion = all_of(
    lambda s: all(atom["converged"] for atom in s["atoms"]),
    threshold("coverage", 80),
)

# Epic level: all tasks converged + integration
epic_criterion = all_of(
    lambda s: all(task["converged"] for task in s["tasks"]),
    threshold("integration_score", 85),
    lambda s: s["docs_updated"],
)
```

### Step 3: Execute Bottom-Up

Start at the leaves. Each converged sub-task feeds its result up to the parent. The parent's `improve()` incorporates sub-task results and addresses cross-cutting concerns.

### Step 4: Track with Genome

Each level's convergence run generates a `ConvergeOutcome` that feeds the genome. Genes governing decomposition strategy, criterion selection, and team patterns all receive fitness signals from the hierarchical execution.

## Design Pattern Mapping

| Convergence Concept | GoF Pattern | GRASP Principle | Systematicity |
|---------------------|-------------|-----------------|---------------|
| `converge()` kernel | Template Method | Protected Variations | 8/10 |
| Criterion combinators | Decorator (layered composition) | Polymorphism | 7/10 |
| Hierarchical decomposition | Composite | Low Coupling | 9/10 |
| VelocityTracker | State | Information Expert | 7/10 |
| Marketplace inheritance | (no direct GoF equivalent) | Pure Fabrication | -- |

## Anti-Patterns

### When NOT to use full-converge

1. **Trivial tasks**: If the task is a single-line fix, converge() adds overhead without value.
2. **Time-critical hotfixes**: Production is down. Fix it, then converge the post-mortem.
3. **Exploration tasks**: When you do not know what "done" looks like, you cannot write a criterion. Use research/exploration first, then converge.
4. **Single-depth sufficiency**: If depth 0 consistently satisfies, the criterion is too loose or the task is too simple for convergence.

### Failure Modes

1. **Criterion drift**: Changing what "done" means mid-convergence. Lock the criterion before starting.
2. **Depth explosion**: Nested converge() without depth caps. Always set `max_depth` at every level.
3. **Score inflation**: Self-scoring tends toward 91. Use per-dimension scoring with the anti-attractor rule.
4. **Over-decomposition**: Breaking atoms into sub-atoms. If a unit of work takes < 5 minutes, it does not need its own convergence loop.

## Evidence

First observed at scale in Gen 381 (3 epics broken into tasks, 5 named agents; the record names those two levels). The pattern produced:
- 6 completed tasks in a single session
- Criterion-based exit gates prevented over-engineering
- Parent-level convergence caught integration gaps that task-level convergence missed

## Integration with RSI

During RSI waves, full-converge operates at the wave level:

```
converge(RSI_SESSION, all_tasks_shipped, rsi_improve, max_depth=8)
  |
  +-- W1: converge(detect, gaps_found, scan)
  +-- W2: converge(analyze, patterns_mapped, deep_analysis)
  +-- W3: converge(build, features_implemented, implement)
  +-- ...
```

Each wave is itself a convergence run. The RSI session is the epic-level convergence. This is the Fibonacci wave protocol expressed as nested converge() calls.
