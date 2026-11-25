---
name: preparing-meetings
description: Rapid-turnaround meeting preparation with parallel research agents, contact dossiers, and structured talking points. Use when preparing for any meeting, call, or event.
status: Active
effort: medium
version: 1.0.0
triggers:
  - "/prep"
  - "prep for meeting"
  - "prepare for call"
  - "meeting prep"
  - "research dossier"
see_also:
  - research-synthesis
  - professional-pdf
  - authentic-writing
---

# /prep — Meeting Preparation

Rapid-turnaround preparation for meetings, calls, and events using parallel research agents.

## Usage

```bash
/prep "Contact Name" from "Company"            # Full prep (10-15 min)
/prep quick "Contact Name"                      # Quick prep (<5 min)
/prep event "Event Name" --theme "topic"        # Event prep (15-20 min)
/prep "Contact Name" --pdf                      # Generate PDF output
/prep "Contact Name" --save                     # Save to docs/preps/
```

## Protocol

### Step 0: Context Gather

If not provided in the command, ask:

1. **Who** is the meeting with? (name, company, role if known)
2. **What** is the purpose? (sales, partnership, interview, catch-up, event)
3. **When** is it? (determines time budget: <10 min = quick, >10 min = full)
4. **Specific asks?** (topics to cover, questions to prepare, materials needed)

### Step 1: Parallel Research Dispatch

Launch up to 4 Task agents simultaneously:

**GRIP-First**: Before dispatching web search agents, check KONO for existing knowledge:
- Prior meeting preps with same contact/company
- Internal context that eliminates the need for external research
- Existing dossiers or relationship history

**Agent 1 — Contact Background**
Search for professional background, role history, LinkedIn activity, recent public statements, talks, and articles. Write findings to a structured section.

**Agent 2 — Company Intel**
Research the company: recent news, funding, tech stack, strategic priorities, and pain points relevant to the user's offering. Include competitive landscape if relevant.

**Agent 3 — Internal Context**
Search GRIP memory (KONO), repo history, and team data for any prior interactions, related projects, or overlapping contacts with this person or company. Check LinkedIn data dump at `~/CodeTonight/PARAMOUNT_BACKUPS/` using `rg` on Connections.csv and Invitations.csv.

**Agent 4 — Strategy Assembly**
Based on findings from other agents, generate strategic talking points, questions to ask, potential objections with responses, and a recommended meeting flow.

For **quick** prep: use a single combined agent instead of 4 parallel ones.

### Step 2: Assemble Prep Document

Structure the output as:

```markdown
# Meeting Prep: {Contact} — {Date}

## Executive Summary
{1 paragraph: who, why, key angle}

## Contact Background
{Role, career trajectory, recent activity, interests}

## Company Intel
{News, funding, tech stack, strategic priorities, pain points}

## Internal Context
{Prior interactions, mutual connections, related projects}

## Strategic Approach
### Talking Points (5)
1. {Point tied to their priorities}
2. ...

### Questions to Ask (3-5)
1. {Open-ended, shows research}
2. ...

### Potential Objections
| Objection | Response |
|-----------|----------|
| ... | ... |

## Key Numbers
{Relevant stats, metrics, data points to reference}
```

### Step 3: Output

| Flag | Action |
|------|--------|
| (default) | Display in conversation |
| `--pdf` | Generate branded PDF via `professional-pdf` skill |
| `--save` | Save to `docs/preps/{contact}-prep-{YYYY-MM-DD}.md` and commit |
| `--pdf --save` | Both |

## Prep Levels

| Level | Trigger | Time | Agents | Depth |
|-------|---------|------|--------|-------|
| quick | `/prep quick` | <5 min | 1 combined | Surface scan, key facts only |
| full | `/prep` | 10-15 min | 4 parallel | Deep research, full dossier |
| event | `/prep event` | 15-20 min | 4 + event protocol | Deep + theme mapping |

## Event Prep: AI RoundTable

When prepping for AI RoundTable events, extend the protocol:

1. Cross-reference Guild member list with LinkedIn connections
2. Build 3 topics mapped to the organiser's framing
3. Each topic: war story from GRIP experience + discussion angle + open question
4. Write 30-second elevator pitch leading with most concrete example
5. Reference: `memory/ai-roundtable.md` for past themes, war stories bank

## Data Sources

| Source | Location | Method |
|--------|----------|--------|
| LinkedIn | `~/CodeTonight/PARAMOUNT_BACKUPS/` | `rg` on Connections.csv, Invitations.csv |
| KONO | `embeddings.db` | Semantic search via recall |
| GitHub | Team data, repo history | `gh` CLI |
| Web | Real-time company/contact research | WebSearch, WebFetch |
| Memory | MEMORY.md, session-context.md | Direct read |

## Time-Pressure Mode

When time is critically short (<10 min before meeting):

1. Skip Agent 2 (company intel) — focus on contact + internal context
2. Reduce talking points to 3
3. Reduce questions to 2
4. Skip objection mapping
5. Output directly in conversation (no PDF/save)
