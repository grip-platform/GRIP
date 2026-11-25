---
name: testing-mode-orchestration
description: Routes QA through human-in-the-loop gates with Devil's Advocate framework. Use when /mode testing is active.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode testing
  - testing mode active
integrates:
  - testing-qa-lifecycle
  - design-principles
  - asking-users
---

# Testing Mode Orchestration

Routes QA lifecycle through human-in-the-loop gates with Devil's Advocate challenge framework. Orchestration layer only -- see `testing-qa-lifecycle/SKILL.md` for test plan templates.

## Protocols

### 1. Domain Classification

Every testing request enters domain routing:

| Signal | Domain | Approach |
|--------|--------|----------|
| "unit test", "integration test" | CODE | Automated test generation |
| "test this feature", "verify" | FEATURE | Test plan + manual verification |
| "test mode", "test skill" | INFRASTRUCTURE | GRIP validation |
| "challenge", "devil's advocate" | KNOWLEDGE | Assumption stress-testing |
| Ambiguous | HALT | AskUserQuestion for domain |

### 2. Human-in-the-Loop Gates

| Gate | Trigger | Purpose |
|------|---------|---------|
| CHECKPOINT | Category completion | Manual test verification |
| REVIEW | Results interpretation | Human approves findings |

### 3. Gate Fatigue Mitigation

- Batch CHECKPOINT: Combine Happy Path + Edge Cases + Error Handling for low-risk features
- Target 4-5 gates per session maximum
- Security, Performance, Regression always get individual gates

### 4. Devil's Advocate Protocol

At every checkpoint, challenge assumptions:

1. What could go wrong that we haven't tested?
2. What edge case would break this?
3. What assumption are we making about the input?
4. What happens under load/concurrency?

## Workflows

### Quick (single test case, assumption check)

1. Domain classification
2. Direct execution
3. Output findings

**Broly level**: THREAD (de-escalated)

### Standard (feature QA, test plan execution)

1. Domain classification
2. Generate structured test plan (6 categories)
3. Sequential execution with CHECKPOINT gates
4. Devil's Advocate at each checkpoint
5. REVIEW gate for final interpretation
6. Output report

**Broly level**: CLUSTER (default)

### Deep (cross-domain QA, regression suite)

1. Multi-domain analysis via Broly MESH:
   - Code testing agent
   - Feature verification agent
   - Infrastructure validation agent
   - Knowledge stress-testing agent
2. Cross-domain correlation
3. Comprehensive report with risk assessment

**Broly level**: MESH (escalated)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Testing domain unclear
- Scope of testing needed
- Standards or guidelines to test against
- CRITICAL issues found during testing
- CHECKPOINT gate reached
- REVIEW gate reached

## Output Style

- Results-first with pass/fail indicators
- British English throughout
- No emoji
- Structured report format (summary, findings, risks)
- Include reproduction steps for failures

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for testing mode |
