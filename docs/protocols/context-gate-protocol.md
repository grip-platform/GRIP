---
name: context-gate
description: "Context Gate - Monitors context usage and alerts at 80% threshold before compaction"
---

# Context Gate Protocol

Implements the Context Gate protocol: **When context reaches 80%, HALT and ask user how to proceed.**

## What It Does

1. Monitors context token usage after each agent turn
2. When context exceeds 80% threshold, injects a warning to the user
3. Provides options: flush to memory, compact with summary, or continue

## Why It Matters

Context compaction loses information. The Context Gate ensures:
- User is aware before information loss occurs
- Important context can be preserved to memory files
- User controls when/how compaction happens

## Protocol Reference

From GRIP CLAUDE.md:
```
Context Gate (80%)
When context reaches 80%: HALT. Ask user how to proceed.
```

## Configuration

No configuration needed. Enabled by default when the hook is active.

To adjust threshold (future):
```json
{
  "hooks": {
    "internal": {
      "entries": {
        "context-gate": {
          "enabled": true,
          "threshold": 0.8
        }
      }
    }
  }
}
```

## Behavior

When context > 80%:
- Agent receives a system message about the threshold
- User sees: "Context at X%. Options: /flush (save to memory), /compact (summarize), or continue."

## Integration with session-memory

Works alongside the `session-memory` hook. When user issues `/flush`:
1. session-memory saves current context to `memory/YYYY-MM-DD-slug.md`
2. Context gate acknowledges the save
3. User can then safely `/new` to reset

---

*Part of GRIP Safety Gates.*
