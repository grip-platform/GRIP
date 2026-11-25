---
name: adr-generation
description: Create and manage Architecture Decision Records (ADRs). Use when recording a significant architectural decision, documenting a technology choice with its rationale, or asked for an ADR or decision record.
---

# ADR Generation

Architecture Decision Record creation and management.

## When to Use

- Recording significant architectural decisions
- Documenting technology choices with rationale
- Providing context for future developers
- Architect-mode sessions

## ADR Template

```markdown
# ADR-{NUMBER}: {TITLE}

## Status
{Proposed | Accepted | Deprecated | Superseded by ADR-X}

## Context
What is the issue that we're seeing that motivates this decision?

## Decision
What is the change that we're proposing and/or doing?

## Consequences

### Positive
- What becomes easier?

### Negative
- What becomes harder?

### Risks
- What could go wrong?
```

## Protocol

### Step 1: Identify Decision Point

Decisions worth recording:
- Technology/framework selection
- Architecture pattern choice
- API design decisions
- Data model changes
- Security approach selection

### Step 2: Gather Context

- What constraints exist?
- What alternatives were considered?
- What trade-offs are involved?
- Who are the stakeholders?

### Step 3: Document Decision

Use the template above. Key principles:
- **Immutable**: ADRs are not edited after acceptance (supersede instead)
- **Numbered**: Sequential for easy reference
- **Linked**: Reference related ADRs

### Step 4: Review

- Share with stakeholders for feedback
- Update status from Proposed to Accepted
- Link from relevant code comments

## Integration

Used by: architect mode. See also: design-principles.
