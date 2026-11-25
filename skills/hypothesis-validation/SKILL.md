---
name: hypothesis-validation
description: Systematically test assumptions by stating them as falsifiable hypotheses with a validation plan. Use when an assumption is driving a decision and needs testing before it is trusted.
---

# Hypothesis Validation

Systematic approach to testing assumptions and validating hypotheses.

## Core Principle

> Every assumption is a hypothesis until validated.

## Hypothesis Formulation

### Structure

```markdown
## Hypothesis: [H-ID]

**Statement**: If [condition], then [expected outcome], because [reasoning].

**Type**: [Technical | Business | User | Performance]

**Falsifiable**: [Yes/No - if No, reformulate]

**Validation Method**: [How will we test this?]

**Success Criteria**: [What would confirm/refute?]

**Confidence Before Test**: [1-10]
```

### Examples

**Technical Hypothesis**:
> If we add database indexing on user_id, then query time will decrease by >50%, because most queries filter by user_id.

**Business Hypothesis**:
> If we add SSO, then enterprise sign-ups will increase by 20%, because security compliance is the #1 objection.

**Performance Hypothesis**:
> If we cache API responses for 60 seconds, then server load will decrease by 40%, because 80% of requests are identical within that window.

## Validation Protocol

### Step 1: Identify Assumptions

Before any significant work, list assumptions:

```grip
assumptions.identify⟿ {
    1. What do we believe to be true?
    2. What are we taking for granted?
    3. What would make us wrong?
    4. Which assumptions are highest risk?
}
```

### Step 2: Prioritize for Testing

| Criterion | Weight | Score 1-5 |
|-----------|--------|-----------|
| Impact if wrong | 3x | How bad if assumption fails? |
| Uncertainty | 2x | How confident are we? |
| Testability | 1x | How easy to validate? |

**Priority Score** = (Impact × 3) + (Uncertainty × 2) + (Testability × 1)

Test highest scores first.

### Step 3: Design Validation

For each high-priority hypothesis:

```markdown
## Validation Plan: [H-ID]

**Hypothesis**: [statement]

**Test Method**:
- [ ] [Specific action to test]
- [ ] [Data to collect]
- [ ] [Success/failure criteria]

**Minimum Viable Test**: [Quickest way to get signal]

**Resources Required**: [Time, tools, access]

**Timeline**: [When will we have results?]
```

### Step 4: Execute and Document

```markdown
## Validation Results: [H-ID]

**Hypothesis**: [statement]

**Result**: [CONFIRMED | REFUTED | INCONCLUSIVE]

**Evidence**:
- [Data point 1]
- [Data point 2]

**Confidence After Test**: [1-10]

**Implications**:
- If confirmed: [what changes?]
- If refuted: [what changes?]

**Next Steps**: [Action items]
```

## Common Validation Methods

| Method | Best For | Speed | Confidence |
|--------|----------|-------|------------|
| Code review | Technical assumptions | Fast | Medium |
| Unit test | Behavior assumptions | Fast | High |
| Prototype | UX assumptions | Medium | Medium |
| A/B test | Business assumptions | Slow | High |
| User interview | Need assumptions | Medium | Medium |
| Benchmark | Performance assumptions | Fast | High |
| Literature | Best practice assumptions | Fast | Medium |

## ut++ Integration

| Gate | Trigger | Action |
|------|---------|--------|
| Gate 1 | Stating assumption as fact | Convert to hypothesis |
| Gate 3 | Assumption identified | Add to tracking list |

## AskUserQuestion Triggers

**MANDATORY** when:
- High-risk assumption identified
- Validation result is inconclusive
- Hypothesis refuted, need pivot

```text
Question: "Assumption '[X]' is untested but critical. How to proceed?"
Header: "Hypothesis"
Options:
- "Test now" - Validate before continuing
- "Accept risk" - Document and proceed
- "Reformulate" - Rethink the approach
```

## Anti-Patterns

| Anti-Pattern | Problem | Solution |
|--------------|---------|----------|
| Confirmation bias | Only seeking confirming evidence | Actively try to refute |
| Moving goalposts | Changing criteria after test | Lock criteria before test |
| Premature certainty | One data point = proven | Require multiple signals |
| Untestable hypothesis | Can't be falsified | Reformulate with specifics |

## Tracking Template

```markdown
# Hypothesis Tracking: [Project]

| ID | Hypothesis | Priority | Status | Result |
|----|------------|----------|--------|--------|
| H1 | [statement] | 15 | TESTING | - |
| H2 | [statement] | 12 | CONFIRMED | [evidence] |
| H3 | [statement] | 10 | REFUTED | [evidence] |
```

## Related Skills

- `riper-workflow` - Structured analysis phases
- `research-synthesis` - Evidence gathering
- `pre-mortem-facilitation` - What could go wrong

---


