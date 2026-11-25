---
name: strategy-mode
description: Routes strategic assessment through gap analysis, red-teaming, and decision locking. Use when /mode strategy is active.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode strategy
  - strategy mode active
integrates:
  - strat-ops
  - leading-organisation
  - asking-users
  - creating-wizards
  - hitl
  - hitl-decision-matrix
---

# Strategy Mode Orchestration

Routes strategic assessment through gap analysis, red-teaming, and pre-mortem frameworks. Orchestration layer only -- see `strat-ops/SKILL.md` for strategy frameworks and `leading-organisation/SKILL.md` for delegation protocols.

## Protocols

### 1. Strategic Framework Routing

| Signal | Framework | Purpose |
|--------|-----------|---------|
| "analyse position", "gap analysis" | Gap analysis | Current vs desired state |
| "stress test", "red team" | Red-teaming | Adversarial challenge of assumptions |
| "what could go wrong" | Pre-mortem | Prospective failure analysis |
| "lock this decision" | Decision locking | Commit and document decisions |
| Ambiguous | HALT | AskUserQuestion for framework selection |

### 2. Strategic Analysis Protocol

Before any strategic advice:

1. Clarify the decision context
2. Identify stakeholders and constraints
3. Surface hidden assumptions

During analysis:

1. Challenge assumptions explicitly
2. Consider second-order effects
3. Map dependencies and risks

After analysis:

1. Summarise decision criteria
2. Lock decisions with /decision-lock if finalised
3. Document rationale for future reference

### 3. Assumption Surfacing

Every strategic discussion must explicitly name:

- What we assume is true but haven't verified
- What we assume won't change
- What we assume competitors will do
- What we assume about market conditions

## Workflows

### Quick (single strategic question, clarification)

1. Context clarification
2. Quick assessment
3. Output with recommendation

**Broly level**: THREAD (de-escalated)

### Standard (gap analysis, decision assessment)

1. Full strategic analysis protocol
2. Framework selection and execution
3. Assumption surfacing
4. AskUserQuestion for validation
5. Output with decision documentation

**Broly level**: MESH (default -- highest level)

### Deep (organisational strategy, multi-stakeholder decisions)

1. Council-level deliberation via Broly MESH:
   - Market analysis agent
   - Competitive intelligence agent
   - Financial modelling agent
   - Risk assessment agent
   - Stakeholder impact agent
2. Cross-perspective synthesis
3. Decision package with stress testing
4. Decision locking

**Broly level**: MESH (already at highest)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Decision context unclear
- Stakeholders need identification
- Hidden assumptions surfaced that change the framing
- Strategic direction has multiple viable paths
- Decision is irreversible or high-stakes
- Red-team findings challenge core assumptions

## Output Style

- Decision-first format: recommendation then reasoning
- British English throughout
- No emoji
- Use tables for option comparison and stakeholder mapping
- Include second-order effects analysis

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for strategy mode |
