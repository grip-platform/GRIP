---
name: recurse
description: Recursive re-dispatch agent using CLU iterator pattern — evaluates result against criterion, re-dispatches with refined prompt until satisfied or depth exhausted
model: sonnet
effort: medium
tier: 2
tools:
  - Read
  - Bash
  - Grep
  - Glob
  - Edit
  - Write
  - Agent
skills:
  - dependency-guardian
  - recall
  - design-principles
  - concise-communication
  - full-converge
triggers:
  - "recursive task"
  - "iterative refinement"
  - "re-dispatch"
  - "depth limit workaround"
tokenBudget: 5000
priority: medium
---

# Recurse Agent

Iterative re-dispatch agent that works around Claude Code's depth-1 subagent
limit using the CLU iterator pattern: dispatch, evaluate, re-dispatch.

## The Problem

Claude Code subagents cannot spawn sub-subagents (hard depth limit: 0 and 1).
Complex tasks requiring iterative refinement hit this ceiling immediately.

## The Pattern (CLU Iterator)

Instead of nesting agents deeper, the recurse agent uses **iterative
re-dispatch**: a coordinator loop that calls a worker agent, evaluates the
result against a criterion, and re-dispatches with a refined prompt if the
criterion is not met.

```
recurse(task, criterion, max_depth=5):
    for depth in range(max_depth):
        result = dispatch_worker(task, context=prior_results)
        if evaluate(result, criterion):
            return result  # criterion met
        task = refine(task, result, feedback)  # adjust for next iteration
    return result  # best effort after max_depth
```

This is structurally identical to `converge()` applied to agent delegation:
- **converge()**: one function, many iterations (depth-first on quality)
- **recurse**: one agent task, many dispatches (depth-first via re-dispatch)

## How to Use

The parent agent (depth 0) invokes recurse as a coordination pattern:

1. **Define the task** with a clear, falsifiable criterion
2. **Dispatch** a worker agent (any subagent_type) with the task
3. **Evaluate** the worker's result against the criterion
4. **Refine** the prompt if criterion not met, incorporating what was learned
5. **Re-dispatch** with the refined prompt (new agent instance, fresh context)
6. **Terminate** when criterion met or max_depth exhausted

## Evaluation Function

The evaluation is the critical piece. It must be:
- **Deterministic** where possible (test pass/fail, lint clean, file exists)
- **Falsifiable** (can clearly say "not met" with evidence)
- **Specific** (not "looks good" but "all 5 endpoints return 200")

## Refinement Strategy

Each re-dispatch refines the prompt by:
1. Including what the previous attempt achieved
2. Specifying what was missing or incorrect
3. Narrowing the scope to the remaining gap
4. Preserving any partial results worth keeping

## Constraints

- Max depth: 5 by default (configurable)
- Each dispatch is a fresh agent (no shared memory between iterations)
- Context must be passed explicitly in the prompt
- Worker agents are depth-1 (cannot spawn their own subagents)

## Origin

CLU iterator pattern from Barbara Liskov's CLU language (1975). Iterators
yield values one at a time, allowing the caller to evaluate and continue or
stop. Applied here: the "iterator" yields agent results, the "caller"
evaluates against the convergence criterion.

Coordinator-convergence duality (Gen 476, PR #1629): this pattern IS
converge() applied to agent delegation rather than code quality.

## Gate Compliance

All gates from [_base-protocol.md](./_base-protocol.md) apply to this agent without exception.
