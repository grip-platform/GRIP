---
name: architect-mode-orchestration
description: Routes system design through SOLID, GRASP, and ADR frameworks. Use when /mode architect is active.
status: Active
effort: high
version: 1.0.0
triggers:
  - /mode architect
  - architect mode active
integrates:
  - design-principles
  - adr-generation
  - asking-users
---

# Architect Mode Orchestration

Routes architecture decisions through design principles and ADR documentation. Orchestration layer only -- see `design-principles/SKILL.md` for SOLID/GRASP frameworks and `adr-generation/SKILL.md` for decision records.

## Protocols

### 1. Design Workflow

Every architecture decision follows:

1. Understand requirements and constraints
2. Identify decision points
3. Generate options (minimum 2)
4. Evaluate against design principles
5. Document decision in ADR
6. Create diagrams as needed

### 2. Mandatory ADR Triggers

| Decision Type | ADR Required |
|---------------|-------------|
| Technology choice (framework, database, service) | Yes |
| Architecture pattern (monolith vs microservices) | Yes |
| API design decisions | Yes |
| Security approach changes | Yes |
| Significant integration choices | Yes |
| Minor implementation detail | No |

### 3. Diagram Routing

| Request | Diagram Type |
|---------|-------------|
| System context | C4 diagram |
| Interactions between components | Sequence diagram |
| Data models | ER diagram |
| Process flows | Flowchart |

### 4. Options Evaluation

For each architectural option:

| Criterion | Assessment |
|-----------|-----------|
| SOLID compliance | Single responsibility, dependency inversion |
| Scalability | Horizontal/vertical growth path |
| Maintainability | Team cognitive load |
| Cost | Infrastructure and development effort |
| Risk | Failure modes and mitigation |

## Workflows

### Quick (single component design, minor ADR)

1. Requirements clarification
2. Quick evaluation against principles
3. ADR generation
4. Output

**Broly level**: THREAD (de-escalated)

### Standard (service design, API architecture)

1. Design workflow (full 6 steps)
2. Options evaluation matrix
3. ADR with trade-off documentation
4. Diagrams as needed
5. Output architecture package

**Broly level**: PIPELINE (default)

### Deep (multi-service architecture, greenfield design)

1. Multi-perspective analysis via Broly MESH:
   - Domain modelling agent
   - Infrastructure architecture agent
   - API design agent
   - Security architecture agent
   - Cost analysis agent
2. Cross-concern integration
3. ADR suite generation
4. Comprehensive diagram set
5. Architecture decision package

**Broly level**: MESH (escalated)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Decision impacts multiple teams or services
- Trade-offs are significant (performance vs maintainability)
- Choosing between equivalent options
- Finalising ADR for acceptance
- Constraint unclear (budget, timeline, team size)

## Output Style

- Decision-first format: state recommendation, then reasoning
- British English throughout
- No emoji
- Use tables for option comparison
- Include diagrams where they add clarity

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for architect mode |
