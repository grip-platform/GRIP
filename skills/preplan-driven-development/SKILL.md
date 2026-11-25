---
name: preplan-driven-development
description: >
  Structured preplan workflow for GRIP sessions. Write a preplan.md
  before execution: Goal -> Current State -> Gap Analysis -> Steps -> Checklist.
  Extracted from A>> (Andre)'s google-vibe-os workflow.
status: Active
effort: low
version: 1.0.0
triggers:
  - /preplan
  - "create preplan"
  - "preplan driven"
see_also:
  - check-last-plan
  - converge
origin:
  team_member: "A>>"
  project: google-vibe-os
  evidence: ".claude/preplan.md pattern across multiple sessions"
---

# Preplan-Driven Development

Write a structured plan before executing any significant work. The preplan
becomes the session contract — deviations require explicit acknowledgement.

---

## 1. When to Preplan

- Feature development (more than trivial change)
- Refactoring (any cross-file changes)
- Bug fixes with unclear root cause
- Design system updates
- Any work that will span multiple commits

Skip for: single-line fixes, documentation typos, dependency updates.

---

## 2. Preplan Structure

Write to `.claude/preplan.md` (or `~/.claude/cache/last-plan.json` for GRIP):

```markdown
# Preplan: [Feature/Task Name]

## Goal
One sentence describing the desired end state.

## Current State
- What exists today
- What works, what does not
- Key files involved

## Gap Analysis
| Gap | Impact | Effort |
|-----|--------|--------|
| Missing X | Blocks Y | ~2h |
| Broken Z | UX regression | ~30m |

## Steps
1. [ ] First concrete action
2. [ ] Second action (depends on 1)
3. [ ] Third action (independent)
4. [ ] Verification step

## Checklist
- [ ] Tests pass
- [ ] No regressions
- [ ] PR created
- [ ] Documentation updated (if applicable)

## Notes
- Risks, assumptions, open questions
```

---

## 3. Gap Analysis Technique

The gap analysis is the most valuable section. It forces explicit
acknowledgement of what is missing before writing code.

### How to Identify Gaps
1. **Read the codebase** — never assume from memory
2. **List what the goal requires** that does not exist yet
3. **Estimate effort** for each gap (forces realistic scoping)
4. **Rank by impact** — high-impact gaps first

### Common Gap Categories
- **Missing infrastructure**: No test setup, no CI, no linting
- **Missing data**: Schema changes needed, migrations
- **Missing UI**: Components, routes, styles
- **Missing integration**: API endpoints, IPC channels, events

---

## 4. Execution Protocol

1. Write preplan before any code changes
2. Review preplan (self or team)
3. Execute steps in order, checking off as completed
4. If a step reveals new gaps, update the preplan
5. Deviation from plan requires a note in the preplan
6. On completion, the preplan becomes the PR description base

---

## 5. Integration with GRIP

- `/preplan` command creates the preplan template
- Preplan is saved to `cache/last-plan.json` for session linkage
- On session resume, previous preplan is loaded automatically
- Convergence loops reference preplan steps as success criteria

---

## 6. Benefits Observed (A>>'s Evidence)

- 300+ line CLAUDE.md in google-vibe-os built from preplan iterations
- Gap analysis caught missing Electron security hardening before it became a bug
- Swiss Nihilism design system gaps identified before implementation
- Preplan serves as living documentation during development
