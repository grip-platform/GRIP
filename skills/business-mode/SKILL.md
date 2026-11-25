---
name: business-mode
description: Routes founder decisions through strategy, team, and financial frameworks with reversibility gates. Use when /mode business is active and business decisions arise.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode business
  - business mode active
integrates:
  - leading-organisation
  - strat-ops
  - legal-ops
  - asking-users
  - creating-wizards
  - hitl
  - hitl-decision-matrix
---

# Business Mode Orchestration

Routes founder decisions through appropriate frameworks based on reversibility, impact, and domain. Orchestration layer only -- see `leading-organisation/SKILL.md` for delegation protocols and `strat-ops/SKILL.md` for strategy frameworks.

## Protocols

### 1. Decision Routing

Every business decision enters the reversibility gate:

```text
REVERSIBLE? --> YES --> Delegate (70% rule applies)
     |
     NO
     |
     v
IRREVERSIBLE --> HALT --> AskUserQuestion with options + trade-offs
```

Irreversible decisions require explicit user confirmation before proceeding.

### 2. Strategic Coordination

Route to the correct framework based on context:

| Context Signal | Route To | Framework |
|----------------|----------|-----------|
| "analyse position", "gap analysis" | strat-ops | Strategic gap analysis |
| "stress test", "red team" | strat-ops | Red-teaming decisions |
| "what could go wrong" | strat-ops | Pre-mortem facilitation |
| "lock this decision" | strat-ops | Decision locking |
| "contract", "agreement" | legal-ops | Legal document generation |
| "delegate", "team" | leading-organisation | Delegation protocol |

### 3. Team Leadership (70% Rule)

Delegation assessment before every task:

| Question | If YES | If NO |
|----------|--------|-------|
| Can someone do this 70% as well? | Delegate | Founder handles |
| Is this architectural? | Founder decides + documents | Team handles |
| Does it affect a client directly? | Manager involved | Team autonomous |
| Is it a hiring/firing decision? | Founder only | N/A |

### 4. Financial Awareness

When financial decisions arise:

1. Surface cash runway implications
2. Quantify unit economics impact
3. Present growth vs profitability trade-off
4. AskUserQuestion for final decision

## Decision Matrix

| Domain | Owner | Artifact | AskUser? |
|--------|-------|----------|----------|
| Architecture | Founder | ADR document | If trade-offs unclear |
| Client contract | Founder + Legal | Contract via legal-ops | Always |
| Hiring | Founder | Job spec + comp band | Always |
| Day-to-day ops | Delegate | Status update | No |
| Strategic pivot | Founder | Decision doc | Always |
| Compensation | Founder | Comp framework | Always |
| Process change | Manager | Process doc | If irreversible |

## Workflows

### Quick (reversible operations)

1. Decision routing gate
2. Identify as reversible
3. Delegate recommendation with 70% assessment
4. Output action items

**Broly level**: THREAD (de-escalated)

### Standard (hiring, compensation, client strategy)

1. Decision routing gate
2. Identify as irreversible or high-impact
3. AskUserQuestion with options and trade-offs
4. Route to appropriate framework (strat-ops, legal-ops, or leading-organisation)
5. Generate decision artifact
6. Output with next steps

**Broly level**: FORK (default)

### Deep (pivot, fundraising, restructuring)

1. Decision routing gate
2. Multi-framework analysis via Broly CLUSTER:
   - Financial modelling agent
   - Strategic assessment agent
   - Risk analysis agent
3. Synthesise findings
4. AskUserQuestion with consolidated options
5. Decision locking via strat-ops
6. Output comprehensive decision package

**Broly level**: CLUSTER (escalated)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Hiring or termination decisions
- Team restructuring proposals
- Strategic pivot considerations
- Funding or investment decisions
- Technical authority tension (coding vs leading balance)
- Compensation changes
- Client contract terms
- Irreversible operational changes

## Output Style

- Direct, no corporate speak
- Decision-first format: state the recommendation, then the reasoning
- Use tables for option comparison
- Quantify where possible (runway months, revenue impact, team utilisation)
- British English throughout
- No emoji

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for business mode |
