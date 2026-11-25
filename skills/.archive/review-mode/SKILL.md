---
name: review-mode-orchestration
description: Routes unified review across code, content, documents, and designs. Use when /mode review is active.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode review
  - review mode active
integrates:
  - reviewing-contributions
  - design-principles
  - asking-users
---

# Review Mode Orchestration

Routes review work across code, content, document, and design domains with structured output. Orchestration layer only -- see `reviewing-contributions/SKILL.md` for review standards and `design-principles/SKILL.md` for evaluation criteria.

## Protocols

### 1. Domain Classification

| Signal | Domain | Focus |
|--------|--------|-------|
| "code review", "PR review" | CODE | Logic, architecture, security, performance |
| "content review", "edit" | CONTENT | Clarity, accuracy, voice, audience fit |
| "document review", "docs" | DOCUMENT | Completeness, consistency, formatting |
| "design review", "UX" | DESIGN | Usability, accessibility, hierarchy |
| Ambiguous | HALT | AskUserQuestion for domain |

### 2. Structured Review Output

Every review follows this format:

1. **Summary**: One-paragraph overview and overall assessment
2. **Strengths**: What works well (specific, with references)
3. **Issues**: Severity-tagged findings with suggestions
4. **Suggestions**: Non-blocking improvements
5. **Questions**: Items needing author clarification

### 3. Severity Classification

| Level | Meaning | Blocks Approval |
|-------|---------|-----------------|
| CRITICAL | Security, data loss, crashes | Yes |
| IMPORTANT | Logic errors, poor UX | Yes |
| MINOR | Small quality improvements | No |
| NIT | Purely stylistic | No |

### 4. Review Principles

- Be specific (line numbers, examples)
- Suggest fixes, don't just point out problems
- Acknowledge good work
- One pass per severity level (critical first)
- Review the work, not the person

## Workflows

### Quick (single file, quick feedback)

1. Domain classification
2. Single-pass review
3. Structured output

**Broly level**: THREAD (de-escalated)

### Standard (PR review, document review)

1. Domain classification
2. AskUserQuestion for scope and standards
3. Multi-pass review (critical → important → minor → nit)
4. Structured output with actionable suggestions
5. Summary recommendation (approve/request changes)

**Broly level**: FORK (default)

### Deep (large PR, multi-domain review)

1. Multi-domain analysis via Broly CLUSTER:
   - Code review agent
   - Documentation review agent
   - Design consistency agent
2. Cross-domain findings correlation
3. Consolidated structured output
4. Priority-ranked action items

**Broly level**: CLUSTER (escalated)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Review domain unclear
- Scope of review needed (full vs specific concern)
- Standards or guidelines to review against
- CRITICAL issues found (confirm before continuing)
- Review scope expands beyond original request

## Output Style

- Findings-first with severity badges
- British English throughout
- No emoji
- Reference line numbers and file paths
- Constructive tone throughout

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for review mode |
