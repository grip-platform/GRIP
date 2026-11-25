---
description: Unified one-shot save of all GRIP memory subsystems (insights, genome, instance, KONO, session context).
disable-model-invocation: false
---

# /save -- Unified State Persistence

One command to save all GRIP memory infrastructure mid-session.

## Usage

```bash
/save                              # Auto-detect achievement from git log
/save "completed the auth refactor" # Explicit achievement message
```

## What It Saves

| # | Subsystem | What | File |
|---|-----------|------|------|
| 1 | Insights | Flush accumulated insight stack | `learning/converge-metrics.jsonl` |
| 2 | DNA | Genome sync, time-decay, KONO attestation | `cache/genome.json` |
| 3 | Instance | Session serialization with achievement | `projects/*/grip/index.json` |
| 4 | Memory | Inject the compact brief into the memory index | `projects/*/memory/MEMORY.md` |

**`/save` does NOT write `rules/session-context.md`** (corrected 2026-09-11; this row
claimed it did). `grip_save.py:612` runs `unified_memory_brief.py --inject-memory
--skip-if-no-instance`, and only the separate `--write-rules` flag reaches
`write_rules_file`. That file is written at session boundaries instead —
`hooks/startup/grip.sh:545`, `hooks/session-end.sh:578`, and
`lib/grip_recall.py:807` behind a 5-minute freshness guard. Verified by measurement:
the file's mtime was unchanged across a save that reported all 12 steps passed.
| 5 | KONO | Episodic session summary to embeddings DB | `embeddings.db` |

## Implementation

Load and execute the saving-state skill:

```
Skill(skill="saving-state")
```

Or run the orchestrator directly:

```bash
grip-py ~/.claude/lib/grip_save.py
grip-py ~/.claude/lib/grip_save.py "achievement message"
```

## When to Use

- Before `/compact`
- After completing a significant milestone
- Before switching projects
- During RSI breathing pauses
- Anytime you want a checkpoint

Supersedes `/save-session-state` (which only saves instance JSON).
