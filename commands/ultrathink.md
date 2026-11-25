---
description: Always-active reasoning protocol with mandatory user consultation. Harnesses native extended thinking and adds GRIP's six mechanical guards. With a question, runs a three-vendor trio (Fable 5.1, GPT-6 Astra, DeepSeek v4 Pro) that debates and falsifies before you synthesise.
disable-model-invocation: false
harness: universal
---

# /ultrathink

Two forms:

| You type | What happens |
|----------|--------------|
| `/ultrathink` | Prints the protocol: the six guards, the workflow phases and the insight-block format. No model is called. |
| `/ultrathink <question>` | Runs the trio deliberation, then you write the synthesis. |

## Bare `/ultrathink`: the protocol

Load and execute the ultrathink skill:

```
Skill(skill="ultrathink")
```

`/ultrathink` is the always-active reasoning protocol. It harnesses Claude
Code's native extended thinking as substrate and adds GRIP's six mechanical
guards on top.

See `skills/ultrathink/SKILL.md` for the full guard table, workflow phases,
insight-block format, and changelog.

## `/ultrathink <question>`: the trio

1. Run the trio through Bash:

   ```bash
   bin/grip-ultrathink "<question>"
   ```

   If this session runs on Fable 5.1, add `--session-fable`: you hold the Fable
   seat, so no second Fable call is made. To let the other two seats test your own
   view, write it to a file first and add `--fable-position-file <file>`.

2. Read the synthesis brief it prints and write the synthesis it asks for. Keep each
   claim with the seat that made it, settle each disagreement on evidence or name it
   as open, never average the positions, and name any degraded seat.

3. Show the footer: the rung that answered for each seat, latency, degraded seats,
   the record path and the IDR leaf.

Exit 2 means the trio refused, because fewer than two seats answered. Say so
plainly, and never present one model's view as a council's.

"Always" means whenever /ultrathink deliberates, not a council on every turn: the
six guards stay always-on and unchanged. The ladder, the vendor rule, the cost
posture and that interpretation are in `skills/ultrathink/SKILL.md`, under "Trio
deliberation".

## Cross-harness invocation

| Harness | Invocation |
|---------|------------|
| Claude Code | `/ultrathink` |
| Aider | `run ultrathink`, `execute ultrathink skill`, or `Skill(skill="ultrathink")` |
| OpenCode | Auto-loaded from `~/.claude/skills/`; use `/skill ultrathink` |
| Gemini CLI | `/ultrathink` if configured in `.gemini/settings.json` context |
| Cursor / Codex / other | Reference `AGENTS.md` and `skills/ultrathink/SKILL.md` |
| Any harness | `bash scripts/skill-dispatch.sh ultrathink` returns the protocol body |
| Any harness with a shell | `bin/grip-ultrathink "<question>"` runs the trio |
