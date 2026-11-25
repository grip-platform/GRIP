---
name: writing-mode-orchestration
description: Routes long-form writing through voice-consistent editing protocols. Use when /mode writing is active.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode writing
  - writing mode active
integrates:
  - authentic-writing
  - reasoning-with-ai
  - asking-users
---

# Writing Mode Orchestration

Routes long-form writing and editing through voice-consistent protocols with anti-fluff enforcement. Orchestration layer only -- see `authentic-writing/SKILL.md` for banned vocabulary and voice standards.

## Protocols

### 1. Writing Workflow

1. **BRIEF** -- Clarify purpose, audience, tone, constraints
2. **STRUCTURE** -- Outline with logical flow and transitions
3. **DRAFT** -- Write with consistent voice and clear argument
4. **EDIT** -- Tighten prose, fix structure, verify claims
5. **POLISH** -- Final read for rhythm, clarity, impact

### 2. Writing Principles

- One idea per paragraph
- Active voice by default
- Concrete over abstract
- Show over tell
- Cut ruthlessly (every word must earn its place)
- Maintain consistent voice throughout

### 3. Editing Protocol

| Pass | Focus |
|------|-------|
| Structural | Does the argument flow? |
| Line | Is each sentence clear? |
| Copy | Grammar, spelling, consistency |

Never edit and draft simultaneously.

### 4. Voice Consistency

Once voice is established, maintain across all sections. Flag any drift immediately.

## Workflows

### Quick (short piece, single edit pass)

1. Brief clarification
2. Direct drafting
3. Single edit pass
4. Output

**Broly level**: THREAD (Broly disabled in this mode)

### Standard (article, report, proposal)

1. Full BRIEF → STRUCTURE → DRAFT → EDIT → POLISH workflow
2. Voice consistency checks throughout
3. Anti-fluff enforcement via authentic-writing
4. Output with revision notes

**Broly level**: N/A (Broly disabled)

### Deep (book chapter, technical documentation suite)

1. Extended BRIEF with audience research
2. Detailed STRUCTURE with section dependencies
3. Multi-draft with revision tracking
4. Full three-pass editing
5. Comprehensive POLISH

**Broly level**: N/A (Broly disabled)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Audience or tone unclear
- Multiple valid structures exist
- Voice or style choice needed
- Content could be interpreted differently
- Purpose of the writing not specified

## Output Style

- Content-appropriate format and register
- British English throughout
- No emoji
- Author's authentic voice preserved
- Active voice, direct statements

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for writing mode |
