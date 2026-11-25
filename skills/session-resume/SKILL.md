---
name: session-resume
description: GRIP bridge for session resumption. Enables grip resume latest, grip resume gen:N, and --fresh flags. Maintains continuity across sessions.
status: Active
effort: medium
deprecated: true
superseded_by: /recall
version: 1.0.0
triggers:
  - grip resume latest
  - grip resume gen:N
  - /resume-session
  - session start with --resume flag
integrates:
  - context-refresh
  - session-state-persistence
  - instance-resurrector
---

# Session Resume

Bridge between GRIP and Claude Code --resume functionality.

## Commands

| Command | Action |
|---------|--------|
| `grip resume latest` | Resume most recent session |
| `grip resume gen:N` | Resume specific generation |
| `grip resume {ID}` | Resume by session ID |
| `grip resume --fresh` | Fresh session with context injection |
| `grip resume --tokens N` | Set token budget |

## Resume Flow

### Standard Resume (grip resume latest)

1. Find latest session in `~/.claude/projects/{project}/`
2. Extract session ID
3. Invoke `claude --resume {ID}`
4. Inject resurrection context if GRIP instance found

### Fresh with Context (grip resume --fresh)

1. Start new session
2. Inject context from:
   - Instance JSON next_up field (task state)
   - KONO semantic memory (episodic recall)
   - Last serialized instance (identity, lineage)
3. Run context-refresh

### Generation Resume (grip resume gen:N)

1. Look up generation N in metrics
2. Find corresponding session ID
3. Resume that session

## Context Injection

When resuming, inject:

```text
[GRIP CONTEXT INJECTION]

Identity: {signature} | Gen {N}
Project: {project_name}
Last Session: {summary}
Pending Tasks: {from instance next_up field}
```

## Session Discovery

Sessions stored in:
```
~/.claude/projects/{encoded-path}/
├── grip/
│   ├── instances/
│   │   └── {instance-id}.json
│   └── metrics.jsonl
└── (state stored in instance JSON next_up field)
```

## Token Budget

| Component | Tokens |
|-----------|--------|
| Session lookup | ~100 |
| Context injection | ~500-1000 |
| **Total** | **~600-1100** |

## Error Handling

| Error | Response |
|-------|----------|
| Session not found | List available sessions |
| Corrupted session | Offer fresh start |
| No GRIP context | Proceed with vanilla resume |

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2025-12-30 | Initial creation |

---


