---
name: code-mode
description: Routes software development through design principles and verification gates. Use when /mode code is active.
status: Active
effort: high
version: 1.0.0
triggers:
  - /mode code
  - code mode active
integrates:
  - feature-complete
  - design-principles
  - code-agentic
  - asking-users
  - creating-wizards
  - hitl
---

# Code Mode Orchestration

Routes software development through SOLID, GRASP, DRY, KISS, YAGNI, and YSH design principles with verification gates. Orchestration layer only -- see `feature-complete/SKILL.md` for the 7-phase workflow and `code-agentic/SKILL.md` for verification gates.

## Protocols

### 1. Requirements Gate

Before any implementation:

1. Verify requirements are clear
2. If confidence < 99.9999999%: AskUserQuestion
3. Consider existing patterns in the codebase
4. Plan approach before coding

### 2. Implementation Routing

| Signal | Route To | Framework |
|--------|----------|-----------|
| "build feature", "implement" | feature-complete | 7-phase workflow |
| "refactor", "restructure" | design-principles | SOLID/GRASP evaluation |
| "fix bug", "debug" | code-agentic | Verification gates |
| "test", "e2e" | e2e-test-generation | Test infrastructure |
| "PR", "pull request" | pr-automation | PR creation |
| Ambiguous | HALT | AskUserQuestion for scope |

### 3. Parallel Research Pattern (Debugging/Investigation)

Calibrate sub-agent count by task difficulty:

| Difficulty | Agents | Strategy |
|------------|--------|----------|
| Simple (clear error, known file) | 1 | Direct fix |
| Medium (unclear root cause) | 3 | Each searches different area (logs, code, tests) |
| Hard (cross-system, intermittent) | 5-10 | Uncorrelated context windows with different hypotheses |

Use Task tool with `run_in_background: true` for parallel agents. Each agent gets a distinct search strategy to maximize coverage with uncorrelated approaches.

### 4. Design Principles Enforcement

Active on ALL code changes:

| Principle | Check |
|-----------|-------|
| SOLID | Single responsibility, open/closed, Liskov, interface segregation, dependency inversion |
| GRASP | Information expert, creator, controller, low coupling, high cohesion |
| DRY | No duplication -- extract duplicated code into shared functions |
| KISS | Simplest solution that works -- function length <= 50 lines |
| YAGNI | Only build what is needed now -- no unused imports |
| YSH | When pattern proven 2x (two independent instances), abstract NOW, through the promotion gate (dialectical inverse of YAGNI) |
| BIG-O | O(n log n) ceiling; nested loops flagged. Use `# O(1):` annotation for bounded loops |
| CC | Cyclomatic complexity: STRICT 10/20, DEFAULT 15/30, RELAXED 20/40 |

### 5. Quality Enforcement (automatic)

8 principles, 3 enforcement tiers:

| Tier | Hook/Mechanism | Principles | Action |
|------|----------------|------------|--------|
| Mechanical gate | `quality-pretool-hook.py` | DRY, KISS, BIG-O, YAGNI | **DENY** on violation |
| Mechanical gate | `cc-gate-hook.py` | CC | **DENY** on violation (trust-modulated) |
| Agent enforcement | `solid-enforcer`, `grasp-enforcer` | SOLID, GRASP | Deep analysis via Agent tool |
| Convergence gate | `principles_criterion.py` | All 8 (SOLID, GRASP, DRY, KISS, YAGNI, YSH, BIG-O, CC) | Convergence loop criterion |
| Secondary signal | `quality-posttool-hook.py` | DRY, KISS, BIG-O, YAGNI | PostToolUse signal (PreToolUse is primary) |

5 principles have mechanical PreToolUse gates (DENY). 2 have agent-based deep analysis. All 8 feed into convergence scoring. No advisory-only path exists for any principle with a fast heuristic.

### 6. Post-Implementation

1. Run tests if available
2. Consider e2e-test-generation for critical paths
3. PR automation via /create-pr when ready

## Workflows

### Quick (single file fix, minor change)

1. Requirements verification
2. Direct implementation with design principles
3. Run existing tests

**Broly level**: THREAD (de-escalated)

### Standard (feature, refactor, bug fix)

1. Requirements gate
2. Route to appropriate framework
3. Implementation with verification gates
4. Test execution
5. Output with summary

**Broly level**: FORK (default)

### Deep (cross-codebase refactor, large feature)

1. Requirements gate
2. Multi-framework analysis via Broly CLUSTER:
   - Architecture assessment agent
   - Implementation agent
   - Testing agent
3. Parallel implementation streams
4. Integration testing
5. Consolidated output

**Broly level**: CLUSTER (escalated)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Requirements unclear or ambiguous
- Scope of change not defined
- Multiple valid implementation approaches
- Architecture decision with trade-offs
- Destructive operation (delete, overwrite)
- Test strategy unclear

## Output Style

- Code-first, minimal explanation unless asked
- British English in comments and documentation
- No emoji
- Include file paths and line numbers in references

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for code mode |
