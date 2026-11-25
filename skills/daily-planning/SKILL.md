---
name: daily-planning
description: Structured workflow for daily task prioritisation and team assignment, ordering work by urgency and impact. Use for a daily planning session or when assigning a day's tickets across a team.
---

# Daily Planning

Structured workflow for daily task prioritisation and team assignment.

## Purpose

Enable efficient daily planning sessions that:
- Prioritise tasks by urgency and impact
- Decompose strategic work into actionable sub-tickets
- Assign work to team members based on complexity
- Generate documentation for parallel GRIP sessions

## Triggers

- `/mode planning` - Activate planning mode
- `/plan` - Quick planning shortcut
- `/daily` - Start daily planning
- "daily planning", "morning standup", "assign tasks"

## Workflow

```
┌─────────┐    ┌────────────┐    ┌───────────┐    ┌────────┐    ┌──────────┐
│ Gather  │───→│ Prioritise │───→│ Decompose │───→│ Assign │───→│ Document │
└─────────┘    └────────────┘    └───────────┘    └────────┘    └──────────┘
```

### Step 1: Gather

Prompt user for task list. Accept unordered input.

**Prompt**: "List the tasks for today. I'll help prioritise and assign."

Tasks may include:
- Single-action items
- Multi-step projects
- Administrative tasks
- Strategic initiatives

### Step 2: Prioritise

Categorise tasks into hierarchy:

| Priority | Criteria | Action |
|----------|----------|--------|
| **1. Urgent** | Time-sensitive, blocking others | Do first |
| **2. Revenue** | Directly impacts income | High priority |
| **3. Deliverables** | Committed to stakeholders | Schedule today |
| **4. Strategic** | Planning, architecture | Decompose further |

### Step 3: Decompose

For **Strategic** items, break into sub-tickets:

```markdown
## Ticket {N}: {Title}

### Overview
{Brief description and goal}

### Sub-Tickets

#### {N}.1 {Sub-task}
**Owner**: {signature}
**Complexity**: {Easy/Medium/High}
**Output**: {Deliverable description}

**Objective**: {What to achieve}

**Scope**:
- {Area 1}
- {Area 2}

**Questions to Answer**:
- {Question 1}
- {Question 2}

**Deliverable**: `{filename}.md`
```

### Step 4: Assign

Match tasks to team members:

| Complexity | Assignment Rule |
|------------|-----------------|
| High (decisions, architecture) | Team lead |
| Medium (research, implementation) | Developers |
| Easy (admin, routine) | Any available |

Load team configuration from `~/.claude/facts/team.md`:
- Signatures (V>>, F>>, A>>, K>>)
- Interaction modes (direct, confirm-first, explanatory, concise)
- Skill areas

### Step 5: Document

Generate artifacts to output directory:

| File | Purpose |
|------|---------|
| `{date}-daily-plan.md` | Master overview, all priorities |
| `ticket-{n}-{name}.md` | Detailed strategic ticket |
| `assignment-{name}.md` | Individual team member tasks |

## Templates

### Master Plan Template

```markdown
# Daily Plan - {DATE}

## Team
| Sig | Name | Role | Focus |
|-----|------|------|-------|
| ... | ...  | ...  | ...   |

## Urgent (Today)
| # | Task | Owner | Notes |
|---|------|-------|-------|

## High Priority (Revenue)
| # | Task | Owner | Notes |
|---|------|-------|-------|

## Medium Priority (Deliverables)
| # | Task | Owner | Notes |
|---|------|-------|-------|

## Strategic Tickets
| # | Ticket | Plan Link | Sub-tickets |
|---|--------|-----------|-------------|

## Session Distribution
| Session | Owner | Tickets |
|---------|-------|---------|

## End of Day Checklist
- [ ] {deliverable 1}
- [ ] {deliverable 2}
```

### Individual Assignment Template

```markdown
# {Signature} Assignment - {DATE}

## Today's Tasks

### Urgent (Do First)
{tasks with full context}

### Strategic Tickets
{sub-tickets assigned to this person}

## End of Day
Deliver to {lead}:
- [ ] {deliverable 1}
- [ ] {deliverable 2}

*{Signature} - {mode} mode*
```

## Team Configuration

### Loading Team

```bash
# Check for team config
if [[ -f ~/.claude/facts/team.md ]]; then
    # Parse team members, signatures, modes
fi
```

### Solo Mode Fallback

If no team configured, operate in solo mode:
- All tasks assigned to user
- Skip team distribution
- Generate single assignment file

## Output Directory

Default: Current working directory
Override: User specifies path

Recommended structure:
```
{output_dir}/
├── {date}-daily-plan.md
├── ticket-{n}-{name}.md
├── assignment-{name}.md
└── meta-learning-{date}.md (optional)
```

## Integration Points

| Skill | Integration |
|-------|-------------|
| `leading-organisation` | Team coordination context |
| `design-principles` | Technical task assessment |
| `asking-users` | Gather input, confirm assignments |
| `creating-wizards`, `hitl` | Siblings of `asking-users`: multi-step intake, and the dispatch contract that chooses between them |

## Meta-Learning

After each planning session, optionally capture:
- Patterns that worked
- Patterns that need adjustment
- New workflow types discovered
- Team capacity observations

Store in `meta-learning-{date}.md` for future mode improvements.

## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~800 |
| Gather phase | ~300 |
| Prioritise | ~400 |
| Decompose (per ticket) | ~600 |
| Assign | ~300 |
| Document | ~500 |
| **Typical session** | **~3000-4000** |

## Examples

### Example: 4-Person Team, 7 Tasks

**Input**: 7 mixed tasks (2 urgent, 1 revenue, 1 deliverable, 3 strategic)

**Output**:
- 1 master plan
- 3 detailed ticket plans (17 sub-tickets total)
- 4 individual assignments
- 1 meta-learning capture

**Time**: Single planning session

### Example: Solo Developer, 5 Tasks

**Input**: 5 tasks (1 urgent, 4 strategic)

**Output**:
- 1 master plan
- 4 ticket plans with sub-tasks
- 1 assignment file (self)

## Future Workflows (v2.0+)

- **Sprint Planning**: Week/2-week cycles
- **Roadmap Planning**: Quarterly objectives
- **Capacity Planning**: Team velocity tracking

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2025-01-29 | Initial creation from daily planning session |

---

**
