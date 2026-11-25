---
description: Universal convergence loop for any task. Iteratively improves output until criterion met or max_depth reached.
disable-model-invocation: false
harness: universal
---

# /converge

Load and execute the converge skill:

```
Skill(skill="converge")
```

`/converge` is the universal convergence loop. The agent becomes `improve()`,
the task output is `state`, quality assessment is `criterion`, and recursion
does the rest.

See `skills/converge/SKILL.md` for the full protocol, scoring rubric,
velocity/stall detection, genome evolution, and falsification clause.

## Cross-harness invocation

| Harness | Invocation |
|---------|------------|
| Claude Code | `/converge [task]` |
| Aider | `run converge`, `execute converge skill`, or `Skill(skill="converge")` |
| OpenCode | Auto-loaded from `~/.claude/skills/`; use `/skill converge` |
| Gemini CLI | `/converge` if configured in `.gemini/settings.json` context |
| Cursor / Codex / other | Reference `AGENTS.md` and `skills/converge/SKILL.md` |
| Any harness | `bash scripts/skill-dispatch.sh converge` returns the protocol body |
