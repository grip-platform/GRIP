---
name: testing-qa-lifecycle
description: Full QA lifecycle with structured test plans, human-in-the-loop gates, Devil's Advocate framework, and knowledge work testing. Core skill for testing mode.
effort: high
status: Active
version: 1.0.0
triggers:
  - /mode testing
  - "test plan"
  - "QA lifecycle"
  - "devil's advocate"
  - "challenge assumptions"
---

# Testing QA Lifecycle

Full QA lifecycle with structured test plans, three human-in-the-loop gates, Devil's Advocate framework, and knowledge work testing protocols.

**Token Budget:** <=5,500 tokens per activation

---

## A. Test Plan Template System

Generate a structured test plan before ANY testing begins. Six categories, each with a human gate.

### Core Categories (Always Present)

| Category | What It Tests | Human Gate |
|----------|--------------|------------|
| Happy Path | Core functionality works | CHECKPOINT |
| Edge Cases | Boundaries, nulls, empties, limits | CHECKPOINT |
| Error Handling | Graceful failures, error messages | CHECKPOINT |
| Security | Auth, injection, data exposure | REVIEW |

### Domain Extensions (Auto-Selected)

| Domain | Additional Categories | Human Gate |
|--------|----------------------|------------|
| Frontend | Accessibility (WCAG, keyboard, screen readers), Performance (load, responsiveness), Cross-browser | CHECKPOINT, REVIEW, REVIEW |
| Backend | Integration (API contracts, service comms), Regression (existing functionality), Data Integrity (migrations, state) | CHECKPOINT, REVIEW, REVIEW |
| Infrastructure | Resilience (failover, recovery), Configuration (env parity), Rollback (revert safety) | REVIEW, CHECKPOINT, REVIEW |
| Knowledge | Devil's Advocate (assumptions), Pre-Mortem (failure modes), Stress Test (variable extremes) | REVIEW, REVIEW, REVIEW |

Select domain extensions based on what is being tested. Multiple domains can stack.

### Template Output

```markdown
## Test Plan: {Feature Name}
Domain: {frontend|backend|infrastructure|knowledge}

### Core
#### Happy Path
- [ ] {scenario 1} - Expected: {result}
- [ ] {scenario 2} - Expected: {result}

#### Edge Cases
- [ ] {scenario} - Expected: {result}

#### Error Handling
- [ ] {scenario} - Expected: {result}

#### Security
- [ ] {check} - Expected: {result}

### Domain Extensions ({domain})
#### {Extension Category 1}
- [ ] {scenario} - Expected: {result}

#### {Extension Category 2}
- [ ] {scenario} - Expected: {result}

### Devil's Advocate
- [ ] Assumption: {assumption} - Challenge: {what if wrong?}
- [ ] Assumption: {assumption} - Challenge: {what if wrong?}
```

### Usage

1. Identify the feature or decision under test
2. Fill template with domain-specific scenarios
3. Present to user for review before execution
4. Execute sequentially with gates between categories

---

## B. Three HITL Gates

All gates are MANDATORY. Agent cannot proceed without human sign-off.

### Gate Fatigue Mitigation

To prevent rubber-stamping from excessive gates, use batch checkpoints for low-risk categories:

**Batch CHECKPOINT**: Combine Happy Path + Edge Cases + Error Handling results into a single gate when:
- Feature is low-risk (no security/data implications)
- All three categories have < 5 scenarios each
- User has not failed a gate in the current session

```
[BATCH CHECKPOINT: Core Categories]

Happy Path: {summary}
Edge Cases: {summary}
Error Handling: {summary}

AskUserQuestion:
  Header: "Verify"
  Question: "Core categories completed. Review the batch results above."
  Options:
    - "All pass" -> Proceed to domain extensions
    - "Issues found" -> Expand to individual category gates
    - "Retest needed" -> Specify which category
```

**Always individual gates for**: Security, Performance, Regression, and any domain extension marked REVIEW.

**Gate budget per session**: Target 4-5 gates maximum. If session would exceed 6 gates, offer batch option proactively.

### Gate 1: CHECKPOINT (Manual Verification)

**Trigger**: After each test plan category completes.

**Protocol**:

```
[TEST CHECKPOINT: {category}]

Results: {summary of what was tested and outcomes}

AskUserQuestion:
  Header: "Verify"
  Question: "Did {category} testing pass? Verify these results."
  Options:
    - "Pass - verified" -> Proceed to next category
    - "Fail - issues found" -> Log issues, create fix tasks
    - "Partial - needs retest" -> Rerun specific scenarios
```

**Rules**:
- Present specific test results, not vague summaries
- Include evidence (output, screenshots, logs) when available
- Agent cannot auto-pass. Human verification required.

### Gate 2: REVIEW (Result Interpretation)

**Trigger**: Before marking any test suite as complete.

**Protocol**:

```
[TEST REVIEW]

Test Suite: {name}
Passed: X/Y
Failed: {list with details}
Skipped: {list with reasons}

My interpretation: {assessment - what this means for the feature}

AskUserQuestion:
  Header: "Approve"
  Question: "Do you agree with this test interpretation?"
  Options:
    - "Agree - proceed" -> Mark complete
    - "Disagree - discuss" -> Discuss interpretation differences
    - "Rerun needed" -> Rerun with different parameters
```

**Rules**:
- Always state your interpretation explicitly
- Include both passing AND failing results
- Never claim "all tests pass" without showing evidence
- Prevents false confidence in automated results

---

## C. Devil's Advocate Framework

Structured assumption-challenging protocol. Applies to ANY domain.

### Protocol

1. **Identify**: List all assumptions in the current work
2. **Challenge**: For each assumption, ask "What if this is wrong?"
3. **Stress Test**: Find the weakest assumption, probe deeply
4. **Present**: AskUserQuestion with challenged assumptions for human evaluation

### Domain Applications

| Domain | Example Assumption | Challenge |
|--------|-------------------|-----------|
| Code | "This input is always a valid UUID" | What if it's null? Malformed? SQL injection? |
| Strategy | "The market will grow 20% this year" | What if it contracts? What if a competitor pivots? |
| Design | "Users will find the settings menu" | What if they never look at the nav? What about mobile? |
| Architecture | "This service has 99.9% uptime" | What if it's down during peak? What's the cascade? |
| Business | "Customers want this feature" | What if they said yes to be polite? What's actual usage? |

### Output Format

```
[DEVIL'S ADVOCATE]

Assumptions identified:
1. {assumption 1}
2. {assumption 2}
3. {assumption 3}

Weakest assumption: #{N} - {reason it's weakest}

Challenge: {detailed what-if scenario}

AskUserQuestion:
  Header: "Challenge"
  Question: "I've challenged assumption #{N}. Does this change your approach?"
  Options:
    - "Valid concern - adjust" -> Modify approach to address
    - "Acknowledged - accept risk" -> Document risk and proceed
    - "Not relevant" -> Dismiss with reasoning
```

---

## D. QA Pipeline Orchestration

Sequential pipeline with human gates between phases. Use for comprehensive feature testing.

### Pipeline

```
Unit Tests -> [CHECKPOINT] -> Integration Tests -> [CHECKPOINT] ->
E2E Tests -> [CHECKPOINT] -> Manual Verification -> [REVIEW] ->
Regression Check -> [REVIEW] -> Sign-Off -> [FINAL REVIEW]
```

### Phase Protocol

Each phase follows the same pattern:

1. Agent generates or runs tests for that phase
2. Presents results with evidence
3. Human verifies via CHECKPOINT or REVIEW gate
4. Only proceeds to next phase on human approval
5. "Fail" at any gate halts the pipeline

### Adaptive Pipeline

Not all phases apply to every test. Skip phases that don't apply:

| Project Type | Skip Phases |
|-------------|-------------|
| Backend API | Accessibility, E2E (unless API E2E) |
| CLI tool | Accessibility, E2E |
| Knowledge work | All code phases (use Devil's Advocate instead) |
| GRIP infrastructure | Use testing-qa-lifecycle with infrastructure domain |

---

## E. Knowledge Work Testing

Non-code testing protocols for decisions, strategies, and designs.

### Protocols

| Protocol | Use Case | Method |
|----------|----------|--------|
| Devil's Advocate | Challenge any decision | Structured assumption attack (Section C) |
| Red Team | Security or strategy review | Adversarial scenario generation |
| Pre-Mortem | Project risk assessment | "Imagine this failed. Why?" |
| Stress Test | Decision resilience | "What if X changes by 10x?" |

### Red Team Protocol

1. Define the target (strategy, architecture, security posture)
2. Assume adversarial mindset
3. Generate 3-5 attack scenarios
4. Present via AskUserQuestion for human evaluation
5. Document findings and mitigations

### Pre-Mortem Protocol

1. State the project or initiative
2. Declare: "This project failed completely. Why?"
3. Generate 5-7 failure scenarios ranked by likelihood
4. Present via AskUserQuestion
5. For top 3 failures, propose preventive measures

### Stress Test Protocol

1. Identify key variables (users, revenue, load, timeline)
2. Apply 10x multiplier (or 0.1x) to each variable independently
3. Assess: Does the design/decision survive?
4. Present breaking points via AskUserQuestion
5. Document resilience boundaries

---

## Integration with Existing Skills

| Skill | Integration |
|-------|-------------|
| `testing-qa-lifecycle` | GRIP infrastructure validation (modes, skills, hooks) via infrastructure domain |
| `e2e-test-generation` | Use for automated Playwright/Vitest setup in code testing |
| `code-agentic` | Verification gates apply during code test execution |
| `design-principles` | Quality gates (SOLID, DRY, KISS) apply to test code itself |

---

## Quick Reference

### When to use each gate

| Situation | Gate |
|-----------|------|
| Test category completed | CHECKPOINT |
| Full suite completed | REVIEW |
| Assumptions need challenging | Devil's Advocate |

### When to escalate

- Single domain testing: CLUSTER (default)
- Multi-domain (3+): MESH
- Simple verification: THREAD

### Team pattern selection

- Code/Infrastructure testing: pipeline (sequential verification)
- Knowledge/Strategy testing: council (parallel perspectives)
