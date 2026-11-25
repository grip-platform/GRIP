---
name: planning-mode-orchestration
description: Routes daily planning through prioritisation hierarchy and team assignment. Use when /mode planning is active.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode planning
  - planning mode active
integrates:
  - daily-planning
  - leading-organisation
  - asking-users
---

# Planning Mode Orchestration

Routes daily planning through prioritisation hierarchy and team assignment logic. Orchestration layer only -- see `daily-planning/SKILL.md` for planning templates and `leading-organisation/SKILL.md` for delegation protocols.

## Protocols

### 1. Planning Workflow

1. **GATHER** -- Collect task list from user (unordered acceptable)
2. **PRIORITISE** -- Sort into priority hierarchy
3. **DECOMPOSE** -- Break strategic items into sub-tickets
4. **ASSIGN** -- Match tasks to team members
5. **DOCUMENT** -- Generate planning artifacts
6. **DISTRIBUTE** -- Save to output directory

### 2. Prioritisation Hierarchy

| Priority | Category | Description |
|----------|----------|-------------|
| 1 | Urgent | Time-sensitive, must complete today |
| 2 | Revenue | Directly impacts income |
| 3 | Deliverables | Committed outputs to stakeholders |
| 4 | Strategic | Planning, architecture, long-term |

### 3. Assignment Logic

| Complexity | Assignee |
|------------|----------|
| High (technical decisions) | Team lead |
| Medium (research, implementation) | Team members |
| Easy (admin, routine) | Any available |

Load team from `~/.claude/facts/team.md` if exists. Fallback to solo mode.

### 4. Output Artifacts

| Artifact | Purpose |
|----------|---------|
| `{date}-daily-plan.md` | Master overview |
| `ticket-{n}-{name}.md` | Detailed ticket plans |
| `assignment-{name}.md` | Individual assignments |

## Workflows

### Quick (solo daily plan, simple schedule)

1. Quick task gathering
2. Priority sort
3. Single output document

**Broly level**: THREAD (de-escalated)

### Standard (team daily planning, sprint prep)

1. Full GATHER → PRIORITISE → DECOMPOSE → ASSIGN → DOCUMENT workflow
2. Sub-ticket generation for strategic items
3. Individual assignment documents
4. Master plan output

**Broly level**: PIPELINE (default)

### Deep (multi-team coordination, resource allocation)

1. Multi-team planning via Broly MESH:
   - Team capacity assessment agent
   - Priority conflict resolution agent
   - Resource allocation agent
   - Schedule optimisation agent
2. Cross-team dependency mapping
3. Comprehensive planning package

**Broly level**: MESH (escalated)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Task list not yet gathered (prompt for input)
- Priority conflicts between items
- Assignment disputes or capacity issues
- Output directory not specified
- Team configuration unclear

## Output Style

- Action-oriented with clear owners and deadlines
- British English throughout
- No emoji
- Use tables for priority matrices
- Include dependencies between tasks

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for planning mode |
