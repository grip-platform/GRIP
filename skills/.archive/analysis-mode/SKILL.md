---
name: analysis-mode-orchestration
description: Routes structured analysis through RIPER phases with systematic investigation. Use when /mode analysis is active.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode analysis
  - analysis mode active
integrates:
  - riper-workflow
  - reasoning-with-ai
  - asking-users
---

# Analysis Mode Orchestration

Routes structured analysis through RIPER workflow phases (Research, Innovate, Plan, Execute, Review). Orchestration layer only -- see `riper-workflow/SKILL.md` for phase protocols and `reasoning-with-ai/SKILL.md` for articulate-first patterns.

## Protocols

### 1. RIPER Phase Routing

| Phase | Access | Purpose |
|-------|--------|---------|
| RESEARCH | Read-only | Understand current state |
| INNOVATE | Read-only | Brainstorm approaches (optional) |
| PLAN | Limited write | Create specifications |
| EXECUTE | Full access | Implement the plan |
| REVIEW | Read + test | Validate implementation |

Commands:
- `/riper:research [topic]` -- Enter Research phase
- `/riper:plan [feature]` -- Enter Plan phase
- `/riper:execute` -- Enter Execute phase
- `/riper:review` -- Enter Review phase
- `/riper:status` -- Show current phase

### 2. Phase Transition Gates

Mandatory AskUserQuestion at every transition:

| Transition | Gate |
|------------|------|
| Research → Plan | Confirm understanding before planning |
| Plan → Execute | Get approval before implementing |
| Execute → Review | Confirm review findings |

No skipping phases. No premature implementation.

### 3. Analysis Quality

Key principle: No premature implementation.

```text
RESEARCH → Understand fully
    ↓
PLAN → Specify completely
    ↓
EXECUTE → Implement precisely
    ↓
REVIEW → Validate thoroughly
```

## Workflows

### Quick (single variable investigation, diagnostic)

1. Direct Research phase
2. Quick findings
3. Output

**Broly level**: THREAD (de-escalated)

### Standard (system investigation, feature analysis)

1. Full RIPER workflow
2. Phase transition gates at each step
3. Structured output per phase
4. Review with validation

**Broly level**: CLUSTER (default)

### Deep (root cause across systems, comparative analysis)

1. Multi-perspective analysis via Broly PIPELINE:
   - Parallel RIPER phases across subsystems
   - Cross-system correlation agent
   - Root cause synthesis agent
2. Integrated findings
3. Comprehensive analysis report

**Broly level**: PIPELINE (escalated)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- After Research: Confirm understanding before planning
- After Plan: Get approval before executing
- After Execute: Confirm review findings
- Analysis direction could branch multiple ways
- Key finding contradicts prior understanding
- Scope of analysis unclear

## Output Style

- Phase-labelled output (prefix each section with current phase)
- British English throughout
- No emoji
- Evidence-based conclusions with source references
- Structured findings format

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for analysis mode |
