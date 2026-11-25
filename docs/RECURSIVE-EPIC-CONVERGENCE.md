# Recursive Epic Convergence Pattern

Origin: Gen 381. First execution at scale: 3 epics broken into tasks, 5 agents.

## Definition

Apply `converge(state, criterion, improve, max_depth)` at every level of a work hierarchy. The convergence kernel is substrate-independent: it works on epics, tasks, sub-tasks, and atoms identically because it only requires three things: a state to evaluate, a criterion to check, and an improve function to apply.

## Formal Structure

```
converge(epic) = converge(
    state = {tasks: [t1, t2, t3], integration: pending},
    criterion = all_of(all_tasks_converged, integration_passes),
    improve = run_next_unconverged_task,
)

converge(task) = converge(
    state = {sub_tasks: [s1, s2], tests: pending},
    criterion = all_of(all_subs_converged, tests_pass),
    improve = run_next_unconverged_sub,
)

converge(atom) = converge(
    state = {code: draft, tests: none},
    criterion = lambda s: s.tests_pass and s.lint_clean,
    improve = edit_and_test,
)
```

## Properties

### Self-Similarity (Fractal)

The same kernel signature, `converge(state, criterion, improve)`, describes every level, so one function can serve each of them with different arguments. "Fractal" here means that much: four named levels of the same shape (epic, task, sub-task and atom), not an unbounded hierarchy. A session applies the pattern level by level; code does not nest it for you. The deepest nesting a GRIP test exercises is three levels (`tests/test_convergence_integration.py`, "converge(converge(converge(state))) — 3 levels deep").

### Criterion Composition

Parent criteria compose child criteria:

```python
epic_criterion = all_of(
    lambda s: all(task.converged for task in s.tasks),  # child criteria
    threshold("integration_score", 85),                  # cross-cutting
)
```

This is the **Composite pattern** (GoF) applied to convergence criteria.

### Graceful Degradation

When a child hits `max_depth` without satisfying its criterion, the parent can:
1. Accept the best_state (graceful degradation)
2. Switch strategy (adaptive combinator)
3. Skip and log (RSI "blocked > 15 min" rule)
4. Escalate to user (AskUserQuestion)

### Velocity Propagation

VelocityTracker at the parent level aggregates velocity signals from children. If multiple children stall simultaneously, the parent detects systemic issues (wrong decomposition, missing dependency) that child-level tracking cannot see.

## Design Principle Alignment

| Principle | How This Pattern Embodies It |
|-----------|-------------------------------|
| **SOLID/SRP** | Each convergence level has one responsibility: converge its scope |
| **SOLID/OCP** | New task types extend the pattern without modifying the kernel |
| **SOLID/LSP** | Any convergence level can substitute for another (same interface) |
| **GRASP/Polymorphism** | criterion and improve are polymorphic across task types |
| **GRASP/Low Coupling** | Parent knows children only through their criterion (converged or not) |
| **DRY** | One kernel, many applications |
| **KISS** | Three parameters: state, criterion, improve |
| **YAGNI** | No speculative features; each level adds only what its scope needs |
| **YSH** | Pattern empirically observed 3x+ (Gen 348, 366, 381); codified per YSH threshold |

## When NOT to Converge

1. **Unknown criteria**: If you cannot state what "done" looks like, explore first.
2. **Trivial work**: Single-line changes do not need iteration.
3. **Time-critical**: Production incidents need fixes, not convergence loops.
4. **Diminishing returns**: If depth 0 consistently satisfies, remove the loop overhead.

## Genome Embodiment

Gene: `full-converge`
- Component type: `skill`
- Expression contexts: all (no restriction)
- Fitness signals: parent convergence success propagates to child genes
- Domain fitness: weighted across all CUBE domains (pattern is cross-cutting)

## Evidence Base

| Session | Application | Result |
|---------|-------------|--------|
| Gen 348 | Worktree isolation | Proved concurrent session safety |
| Gen 366 | Pre-fix context exploration | Prevented 4 surface-level fix rounds |
| Gen 381 | 3 epics broken into tasks | 6 tasks completed, criterion gates prevented over-engineering |
| Gen 384 | RSI convergence architecture sprint | Full pipeline wiring + skill creation |
