---
name: decision-locking
description: Finalise and document strategic decisions to prevent drift. Use after red-team exercises, gap analysis, or when team reaches consensus.
status: Active
effort: high
version: 1.0.0
triggers:
  - /decision-lock
  - /lock-decision
  - "lock this decision"
  - "finalise the choice"
  - "document this decision"
  - "we've decided"
  - "decision made"
integrates:
  - asking-users
  - creating-wizards
  - hitl
  - strat-ops
  - red-teaming-strategic-decisions
---

# Decision Locking

Finalise and document strategic decisions to prevent drift.

## Purpose

Prevent decision drift. Once a decision is "locked":
- It's documented with rationale
- Alternatives are recorded (why we didn't choose them)
- Accountability is clear
- Review date is set
- Reopening requires explicit process

## When to Use

- After completing red-team or gap analysis
- When team reaches consensus on strategic direction
- Before major resource commitment
- When you catch yourself revisiting decided topics
- When stakeholders need documented decision trail

---

## Protocol

### Step 1: State the Decision

```text
Question: "What decision are you locking?"
Header: "Type"
Options:
- "Strategic direction" - Major positioning/approach
- "Resource allocation" - Budget, team, time
- "Product/service" - What we build/offer
- "Operational" - How we work
```

After selection, capture specific statement:

```text
"State the decision in one clear sentence.

Example: 'We will position as Technical Orchestration partners, not as a dev shop.'"
```

### Step 2: Document Alternatives

```text
"What alternatives did you consider? (List 2-4)"

For each alternative:
1. {Alternative A} - Why rejected: {reason}
2. {Alternative B} - Why rejected: {reason}
```

### Step 3: Capture Rationale

```text
"Why is this the right decision?"

Rationale should include:
- Evidence (data, research, red-team results)
- Alignment (with values, strategy, capabilities)
- Risk assessment (what could go wrong, mitigation)
```

### Step 4: Assign Accountability

```text
Question: "Who owns this decision?"
Header: "Owner"
Options:
- "V>>" - Founder owns
- "Shared" - Multiple owners (specify)
- "Delegated" - Specific team member owns
```

If shared/delegated, specify:
- **Decision owner** - Final say on interpretation
- **Execution owner** - Makes it happen
- **Review owner** - Checks progress

### Step 5: Set Review Date

```text
Question: "When should this decision be reviewed?"
Header: "Review"
Options:
- "30 days" - Short-term tactical
- "90 days" - Quarterly strategic
- "6 months" - Major strategic
- "1 year" - Annual planning
```

### Step 6: Lock and Store

Generate decision document and store in:
`~/.claude/strat-ops/decisions/{date}-{slug}.md`

---

## Decision Document Format

```markdown
# Decision: {Title}

**Status**: LOCKED
**Date**: 2026-01-24
**Owner**: V>>
**Review Date**: April 24, 2026

---

## The Decision

{One clear sentence stating the decision}

---

## Alternatives Considered

| Alternative | Reason Rejected |
|-------------|-----------------|
| {Alt A} | {reason} |
| {Alt B} | {reason} |
| {Alt C} | {reason} |

---

## Rationale

### Evidence
{Data, research, red-team results supporting this decision}

### Alignment
{How this aligns with values, strategy, capabilities}

### Risk Assessment
{What could go wrong, how we'll mitigate}

---

## Accountability

| Role | Person |
|------|--------|
| Decision Owner | {sig} |
| Execution Owner | {sig} |
| Review Owner | {sig} |

---

## Review Protocol

- **Review date**: {date}
- **Review trigger**: {what would cause early review}
- **Success metrics**: {how we know it worked}

---

## Source

{If this resulted from an exercise, link it}

- Red-Team Exercise: {date}
- Document: {path}
- Key assumption validated: {assumption}

---

## Approval

| Approver | Date | Signature |
|----------|------|-----------|
| V>> | 2026-01-24 | ✓ |

---

*Locked by GRIP - decision-locking skill*

```

---

## Decision Index

Maintain index at `~/.claude/strat-ops/decisions/INDEX.md`:

```markdown
# Decision Index

| Date | Decision | Owner | Review | Status |
|------|----------|-------|--------|--------|
| 2026-01-22 | Position as Technical Orchestration | V>> | Apr 2026 | LOCKED |
| 2026-01-24 | Referral + Inbound only sales model | V>> | Jul 2026 | LOCKED |
| 2026-01-24 | 7-Fit model for strategic assessment | V>> | Jul 2026 | LOCKED |
```

---

## Reopening Protocol

Decisions can be reopened if:
1. Review date reached
2. Significant new information emerges
3. Decision owner explicitly reopens

```text
Question: "This decision is LOCKED. Reason for reopening?"
Header: "Reopen"
Options:
- "Review due" - Scheduled review time
- "New information" - Circumstances changed significantly
- "Not working" - Decision isn't producing expected results
- "Keep locked" - Don't reopen
```

**If reopening**:
1. Document original decision
2. Document reason for reopening
3. Run decision-lock protocol again
4. Reference original decision in new document

---

## Integration with Other Skills

### After Red-Team

```text
Red-Team Complete → Key assumptions validated → Lock decision
```

### After Gap Analysis

```text
Gap Analysis → Burning platforms identified → Actions decided → Lock decision
```

### After Pre-Mortem

```text
Pre-Mortem → De-risking actions defined → Lock decision on approach
```

---

---

## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~900 |
| Decision capture | ~400 |
| Document generation | ~500 |
| Storage | ~100 |
| **Total** | **~1,900** |

---

## Related Skills

- `red-teaming-strategic-decisions` - Validates assumptions before locking
- `pre-mortem-facilitation` - Identifies risks before locking
- `strategic-gap-analysis` - Informs decisions to lock
- `strat-ops` - Strategy mode router

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-01-24 | Initial creation (promoted from strat-ops/modes) |

---


