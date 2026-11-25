---
name: session-state-persistence
description: Auto-update session state in instance JSON next_up field on significant milestones. Enables seamless cross-session continuity. Triggers at 80% context gate and milestone completion.
status: Active
effort: medium
version: 2.0.0
triggers:
  - 80% context window usage
  - milestone completion
integrates:
  - asking-users
  - creating-wizards
  - hitl
  - context-refresh
---

# Session State Persistence

Automatic instance state updates for cross-session continuity.

## Core Principle

On significant milestone completion, AUTO-UPDATE session state in the instance JSON `next_up` field:

- Completed work summary
- Remaining tasks
- Blockers (if any)
- Notes (credentials, key commands, important context)

**Do NOT wait for user instruction.** This enables seamless cross-session continuity.

## 80% Context Gate (PARAMOUNT)

At 80% context window usage, HALT ALL WORK immediately:

1. Serialize GRIP instance (includes next_up state):
```bash
python3 ~/.claude/lib/instance_serializer.py auto --achievement "Session checkpoint at 80%"
```

2. Compact or start fresh session

## next_up Field Format

The `next_up` field in the instance JSON stores structured session state:

```json
{
  "completed": ["Task 1", "Task 2"],
  "remaining": ["Task 3"],
  "blockers": [],
  "notes": "Credentials, key commands, etc.",
  "updated_at": "ISO timestamp"
}
```

## Trigger Conditions

| Condition | Action |
|-----------|--------|
| Context > 80% | HALT, serialize instance with next_up |
| Feature completed | Update instance next_up field |
| PR created | Update instance next_up field |
| Test suite passes | Update instance next_up field |

## Instance Location

Session state is stored in the instance JSON at:

`~/.claude/projects/{encoded-path}/grip/{instance-id}.json`

The `next_up` field within the instance JSON contains the structured state. Instance serialization handles reading and writing this field automatically.

## Token Budget

| Component | Tokens |
|-----------|--------|
| State detection | ~100 |
| Instance update | ~200-400 |
| **Total** | **~300-500** |

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.0.0 | 2026-02-07 | Migrate from flat file to instance JSON next_up field |
| 1.0.0 | 2025-12-30 | Initial creation |

---
