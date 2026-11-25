---
name: red-teaming-strategic-decisions
description: Adversarial analysis of a strategic decision to surface blind spots. Use before finalising a major decision or when assumptions behind a strategy need challenging.
---

# Red-Teaming Strategic Decisions

Adversarial analysis of strategic decisions to find blind spots.

## When to Use

- Before finalizing major decisions
- Challenging assumptions in strategy
- Pre-mortem analysis
- Decision-mode and strategy-mode sessions

## Protocol

### Step 1: State the Decision

Clearly articulate:
- What is being decided?
- What assumptions underlie the decision?
- What evidence supports it?
- Who are the stakeholders?

### Step 2: Assign Red Team Perspective

The red team's role is to find reasons the decision is **wrong**:
- Challenge every assumption
- Find counterexamples
- Identify failure modes
- Expose hidden risks

### Step 3: Attack Vectors

| Vector | Question |
|--------|----------|
| **Assumption attack** | What if key assumptions are wrong? |
| **Evidence attack** | Is the evidence sufficient? Cherry-picked? |
| **Incentive attack** | Who benefits from this decision being made? |
| **Second-order attack** | What unintended consequences follow? |
| **Timing attack** | Is this the right time? What changes if we wait? |
| **Reversal attack** | What's the cost of being wrong? Can we undo it? |

### Step 4: Evaluate Findings

| Finding | Severity | Mitigation |
|---------|----------|------------|
| Assumption X is unverified | High | Verify before deciding |
| Risk Y is unmitigated | Medium | Add contingency plan |
| Stakeholder Z not consulted | Low | Brief before execution |

### Step 5: Decide with Eyes Open

After red-teaming, either:
- **Proceed**: Decision is robust, risks are acceptable
- **Modify**: Adjust decision to address key findings
- **Defer**: Need more information before deciding
- **Reject**: Fundamental flaw discovered

## Integration

Used by: decision, strategy modes. See also: decision-locking, pre-mortem-facilitation.
