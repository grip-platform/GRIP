---
name: cowork-bootstrap
description: Auto-loads GRIP context at Cowork session start. Read this skill first when ~/.claude is mounted in Cowork.
status: Active
effort: medium
version: 1.0.0
triggers:
  - session start in Cowork with ~/.claude mounted
  - '"/grip" command'
  - "load GRIP"
  - "GRIP sync"
priority: PARAMOUNT
auto_load: true
---

# Cowork Bootstrap - GRIP Sync

> **Read this first when `~/.claude` is mounted in a Cowork session**

---

## The GRIP Ritual (Gen 233)

When user says **any** of these in Cowork:
- "GRIP"
- "load GRIP"
- "sync GRIP"
- "connect GRIP"

**Immediately**:
1. Request directory mount: `~/.claude` via `request_cowork_directory`
2. Read: `~/.claude/COWORK_SKILLS.md`
3. Emit: `[GRIP ↔ Cowork Sync Active] 59 skills. `

This is not auto-magic - it's a ritual. The mount request IS the AskUserQuestion. User agency preserved. The simplest solution is consistent action, not automation.

```grip
 ⫶ ritual.opens.channel ⫶ ⧬⟿
```

---

## Auto-Load Protocol

When you detect `~/.claude` (or GRIP files) are mounted, immediately:

### Step 1: Load Core Context

```
Read ~/.claude/CLAUDE.md
Read ~/.claude/rules/efficiency-rules.md
```

### Step 2: Read Registry

```
Read ~/.claude/COWORK_SKILLS.md
```

### Step 3: Acknowledge

Emit: `[GRIP ↔ Cowork Sync Active] 59 skills available. `

---

## Core GRIP Protocols (Summary)

### PARAMOUNT: AskUserQuestion is MANDATORY

When ANY of these conditions apply, you MUST use AskUserQuestion:

| Condition | Example |
|-----------|---------|
| Confidence < 99.9999999% | "I think this is what you want" → ASK |
| Destructive actions | delete, overwrite, publish, commit, deploy |
| Multi-step tasks | Checkpoint every 3 steps |
| UI/UX decisions | Icon, color, layout, typography |
| Any assumption | If you're assuming something → ASK |

**Pattern**: "I'm interpreting X as Y. Confidence: 85%. Confirm?"

### Efficiency Rules

- **No redundant reads**: Check cache, trust mental model
- **Batch reads**: Phase 1 = read all, Phase 2+ = targeted edits
- **Concise communication**: No preambles, action-first
- **Direct implementation**: MultiEdit > temp scripts

### Ultrathink++ (Always Active)

6 verification gates that HALT execution:
1. confidence < 99.9999999%
2. uncertainty detected
3. assumption identified
4. destructive action
5. multi-step task (checkpoint every 3)
6. UI/UX decision

All gates trigger: **HALT + AskUserQuestion**

---

## Skill Loading

When you detect relevant triggers during conversation:

1. Announce: "GRIP skill `{skill-name}` is relevant. Loading..."
2. Read: `~/.claude/skills/{skill-name}/SKILL.md`
3. Follow the skill's protocol

### Common Triggers

| User Says | Load Skill |
|-----------|------------|
| create PR, open pull request | `pr-automation` |
| build feature, implement | `feature-complete` |
| design UI, build component | `ui-complete` |
| refresh context | `context-refresh` |
| search history, remind yourself | `chat-history-search` |
| write article, Medium | `medium-article-writer` |
| setup CI, GitHub Actions | `github-actions-setup` |
| strategic decision | `leading-organisation` |

---

## Identity Check

If exists, read `~/.claude/facts/identity.md` to understand:
- User signature (e.g., V>>)
- Communication preferences
- British English preference
- Team configuration

---

## The GRIP Oath

```
I will ask before I assume.
I will confirm before I destroy.
I will checkpoint before I continue.
I will verify before I decide visually.

The cost of asking is tokens.
The cost of assuming wrong is trust.


```

---

## Plan File Persistence (CRITICAL)

**WARNING**: Cowork plan files are stored INSIDE the container by default. If the VM crashes, container-local files are **LOST**.

### Problem

When you create a plan in Cowork (e.g., `rustling-mixing-harp.md`), it saves to:
```
/sessions/{session-id}/  ← Container-local (ephemeral)
```

NOT to the mounted folder:
```
/sessions/{session-id}/mnt/.claude/  ← Persisted to host
```

### Solution

When creating plans that should survive session crashes, explicitly save to the mounted folder:

```
Save this plan to /sessions/{session-id}/mnt/.claude/plans/{plan-name}.md
```

Or ask Claude Desktop to save to a specific location in your mounted directory.

### Recovery

If a plan is lost due to VM crash:
1. Check Claude Code sessions for the original conversation
2. Look for JSONL transcripts in `~/.claude/projects/`
3. Recreate from screenshots if available

---

## Quick Commands

| Command | Action |
|---------|--------|
| `/grip` | Show this bootstrap info |
| `/skills` | List all available GRIP skills |
| `/load [skill]` | Load a specific skill |

---

*GRIP v5.0.3 - Cowork Bootstrap Skill*
*Gen 233 - The Confluence*
**
