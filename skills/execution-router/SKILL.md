---
name: execution-router
description: Routes tasks to optimal execution patterns based on complexity assessment (Direct/Focused/Sprint/Full Mesh)
triggers:
  - /route
  - /execution-plan
  - When planning how to execute a multi-step task
  - When user asks "what's the best way to run this"
category: orchestration
version: 1.0.0
---

# Execution Router

Assesses task complexity across 5 dimensions and routes to the optimal execution
pattern. Prevents over-engineering (YAGNI) when simple patterns suffice, and
ensures the full stack is deployed when genuine complexity warrants it.

## The Problem

GRIP has ~15 execution patterns (converge, broly, sprint-loop, rsi-optimal,
research-council, Fibonacci waves, etc.). Choosing the wrong pattern wastes
tokens (over-engineering) or produces shallow results (under-engineering).

## Complexity Assessment (5 Dimensions)

Score each dimension 1-3:

| Dimension | 1 (Low) | 2 (Medium) | 3 (High) |
|-----------|---------|------------|----------|
| **Novelty** | Known pattern, well-understood | Adapted pattern, some unknowns | Novel algorithm, first principles |
| **Scope** | 1 file or repo | 2-4 repos or components | 5+ repos, cross-system |
| **Duration** | Fits in 1 session (<85% context) | 1-3 sessions | Multi-day, 4+ sessions |
| **Uncertainty** | Clear spec, obvious approach | Some ambiguity, 2-3 valid paths | Research needed, problem unclear |
| **Risk** | Reversible, local impact | Moderate blast radius | Production, multi-user impact |

**Total: 5-15 points**

## Routing Table

| Score | Pattern | What Gets Activated | When |
|-------|---------|-------------------|------|
| **5-7** | **Direct** | `/converge` with criterion | Shell scripts, config changes, simple features, bug fixes with clear root cause |
| **8-10** | **Focused** | `/converge` + hypothesis + tests | Features touching 2-3 files, refactors with clear scope, bug fixes needing investigation |
| **11-13** | **Sprint** | `/rsi-optimal` (ultraplan + auto-rsi + broly + Fibonacci) | Multi-PR work, architectural changes, new subsystems |
| **14-15** | **Full Mesh** | `/rsi-optimal` + research council + MoE + LLM-as-judge + sprint-loop | Novel algorithms, cross-repo architecture, multi-day sprints with 10+ PRs |

## Execution Patterns in Detail

### Direct (5-7)

```
/converge "task description" --criterion "done condition" --depth 3
```

Single loop, single agent, simple criterion. Most tasks fall here.

### Focused (8-10)

```
/converge "task description" \
  --criterion "tests pass AND hypothesis confirmed" \
  --depth 5
```

Adds hypothesis registration and test requirements. Still single-session.

### Sprint (11-13)

```
/rsi-optimal "task description"
```

This already wraps: ultraplan (planning) + auto-rsi (execution) + broly-auto
(multi-model reasoning) + Fibonacci breathing (recovery pauses) +
hypothesis-driven criterion. Multi-session capable.

### Full Mesh (14-15)

```
/rsi-optimal "task description" --with-council --sprint-loop --moe-gate
```

Adds:
- **Research council**: Cross-LLM validation (Gemini + Llama + Claude) for
  novel claims and architecture decisions
- **Sprint-loop**: Autonomous issue-picking from GitHub backlog
- **MoE + LLM-as-judge**: External quality scoring (never self-score)
- **Fibonacci outer loop**: Wave-based breathing with escalating batch sizes

Deploy this when:
- Designing a novel algorithm (needs cross-LLM validation)
- Architecture decisions affecting 5+ repos (needs research breadth)
- Multi-day sprints with 10+ PRs (needs Fibonacci recovery)

## Protocol

When this skill is invoked:

1. **Assess** — Score the task across 5 dimensions. Show the scoring table.
2. **Route** — Map total score to execution pattern.
3. **Confirm** — Present the recommended pattern to the user with rationale.
4. **Launch** — Execute the selected pattern.

## Example Assessment

Task: "Create terminal aliases with KONO preload for GRIP and HAL"

| Dimension | Score | Reason |
|-----------|-------|--------|
| Novelty | 2 | Shell + Python, adapted pattern |
| Scope | 2 | 2 repos (GRIP + HAL) |
| Duration | 1 | Single session |
| Uncertainty | 1 | Clear spec |
| Risk | 1 | Local, reversible |

**Total: 7 -> Direct pattern** (`/converge` with criterion)

## Anti-Patterns

- **Stacking for style**: Using full mesh on a 5-point task wastes 100x tokens
- **Under-routing**: Using Direct on a 14-point task produces shallow results
- **Score inflation**: Scoring uncertainty as 3 because "anything could happen"
  is not honest assessment — score what you actually know about the task

## Integration

The execution-router is the entry point for `/route` and can be called by
other skills (e.g., `/ultraplan`, `/rsi`) to validate their execution strategy
before launch.
