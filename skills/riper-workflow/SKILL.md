---
name: riper-workflow
description: Structured five-phase analysis — Research, Innovate, Plan, Execute, Review — with permission levels per phase. Use when a task needs disciplined phase separation rather than jumping straight to code.
---

# RIPER Workflow

Structured analysis through five phases: Research, Innovate, Plan, Execute, Review.

## The Five Phases

| Phase | Mode | Purpose | Allowed Actions |
|-------|------|---------|-----------------|
| **R**esearch | Read-only | Investigate existing state | Read files, search, ask questions |
| **I**nnovate | Read-only | Brainstorm approaches | Explore options, no commitments |
| **P**lan | Limited write | Create specifications | Write to plans only |
| **E**xecute | Full access | Implement the plan | All actions, following plan |
| **R**eview | Read + test | Validate implementation | Read, run tests, no new code |

## Phase Protocols

### Phase 1: RESEARCH

```grip
research⟿ {
    mode: READ_ONLY
    actions: [Read, Grep, Glob, WebFetch, WebSearch]
    forbidden: [Write, Edit, Bash(destructive)]

    protocol:
    1. Understand current state completely
    2. Identify all relevant files and patterns
    3. Document findings in mental model
    4. Surface assumptions for validation

    exit_criteria:
    - Can explain current architecture
    - All relevant code identified
    - Assumptions documented
}
```

**AskUserQuestion trigger**: Before leaving Research, confirm understanding.

### Phase 2: INNOVATE (Optional)

```grip
innovate⟿ {
    mode: READ_ONLY
    actions: [reasoning, exploration]
    forbidden: [Write, Edit, Bash]

    protocol:
    1. Generate 2-3 possible approaches
    2. Evaluate trade-offs for each
    3. Identify risks and unknowns
    4. Do NOT commit to any approach

    exit_criteria:
    - Multiple approaches documented
    - Trade-offs analyzed
    - Ready for user decision
}
```

**Skip when**: Single obvious approach exists.

### Phase 3: PLAN

```grip
plan⟿ {
    mode: LIMITED_WRITE
    actions: [Read, Write(plans_only)]
    forbidden: [Edit(code), Bash(destructive)]

    protocol:
    1. Document chosen approach
    2. Break into discrete steps
    3. Identify dependencies between steps
    4. Estimate risk per step
    5. Save plan to persistent location

    plan_structure:
    - Goal statement
    - Pre-conditions
    - Steps (numbered, testable)
    - Success criteria
    - Rollback strategy

    exit_criteria:
    - Plan written and saved
    - User has approved plan
    - Steps are atomic and testable
}
```

**MANDATORY**: User must approve plan before Execute.

### Phase 4: EXECUTE

```grip
execute⟿ {
    mode: FULL_ACCESS
    actions: [all]

    protocol:
    1. Follow plan exactly
    2. Execute one step at a time
    3. Verify each step before next
    4. Document deviations from plan
    5. Stop if unexpected issues arise

    guardrails:
    - No improvisation without returning to Plan
    - Checkpoint every 3 steps (ut++ Gate 5)
    - Destructive actions require confirmation (ut++ Gate 4)

    exit_criteria:
    - All plan steps completed
    - No unresolved errors
    - Ready for review
}
```

### Phase 5: REVIEW

```grip
review⟿ {
    mode: READ_AND_TEST
    actions: [Read, Bash(tests_only)]
    forbidden: [Write, Edit]

    protocol:
    1. Run all relevant tests
    2. Verify against success criteria
    3. Check for regressions
    4. Document any gaps
    5. Feed learnings to RSI

    exit_criteria:
    - Tests pass
    - Success criteria met
    - No regressions introduced
    - Learnings documented
}
```

## ut++ Integration

| RIPER Phase | ut++ Gate | Integration |
|-------------|-----------|-------------|
| Research | Gate 1 (confidence) | Verify understanding before leaving |
| Innovate | Gate 3 (assumptions) | Surface and validate assumptions |
| Plan | Gate 5 (multi-step) | Checkpoint every 3 steps |
| Execute | Gate 4 (destructive) | Confirm before changes |
| Review | recursive-learning | Feed outcomes to RSI |

## Commands

```text
/riper:research [topic]  - Enter Research phase
/riper:innovate          - Enter Innovate phase (optional)
/riper:plan [feature]    - Enter Plan phase
/riper:execute           - Enter Execute phase
/riper:execute [step]    - Execute specific step
/riper:review            - Enter Review phase
/riper:status            - Show current phase and progress
```

## Plan Template

```markdown
# RIPER Plan: [Feature/Task Name]

## Goal
[One-sentence description of what we're achieving]

## Pre-conditions
- [ ] [Condition 1]
- [ ] [Condition 2]

## Research Summary
[Key findings from Research phase]

## Chosen Approach
[From Innovate phase, or obvious choice]
Rationale: [Why this approach]

## Steps
1. [ ] [Step 1] - [Verification method]
2. [ ] [Step 2] - [Verification method]
3. [ ] [Step 3] - [Verification method]

## Success Criteria
- [ ] [Criterion 1]
- [ ] [Criterion 2]

## Rollback Strategy
[How to undo if something goes wrong]

## Status
Phase: [RESEARCH|INNOVATE|PLAN|EXECUTE|REVIEW]
Last Updated: [timestamp]
```

## AskUserQuestion Triggers

**MANDATORY** at phase transitions:

```text
Question: "Research complete. Found [summary]. Ready to plan?"
Header: "RIPER"
Options:
- "Proceed to Plan" - I understand the findings
- "More research" - Need to investigate [area]
- "Innovate first" - Want to explore multiple approaches
```

```text
Question: "Plan ready for [feature]. Approve execution?"
Header: "RIPER"
Options:
- "Approve and execute" - Proceed with implementation
- "Modify plan" - Need to adjust steps
- "Back to research" - Need more information
```

## Related Skills

- `reasoning-with-ai` - Articulate-first thinking
- `hypothesis-validation` - Testing assumptions
- `design-principles` - Architecture decisions

---


