---
description: Unified autonomous-loop entry point. Subsumes /auto-ship, /auto-rsi, and /rsi-optimal. Args determine sub-shape — bare /auto ships uncommitted changes, /auto deadline=Nh runs a timed loop, /auto wave-mode runs a Fibonacci-wave RSI sprint.
disable-model-invocation: false
harness: universal
---

# /auto

Load and execute the auto skill:

```
Skill(skill="auto")
```

`/auto` is the unified autonomous-loop entry point. It composes existing
kernels (`/bg-dispatch`, `/converge`, `/ultrado`) and reimplements nothing.

See `skills/auto/SKILL.md` for the full lifecycle, dispatch table, safety
guardrails, and integration points.

## Cross-harness invocation

| Harness | Invocation |
|---------|------------|
| Claude Code | `/auto`, `/auto-ship`, `/auto-rsi`, `/rsi-optimal` |
| Aider | `run auto`, `execute auto skill`, or `Skill(skill="auto")` |
| OpenCode | Auto-loaded from `~/.claude/skills/`; use `/skill auto` |
| Gemini CLI | `/auto` if configured in `.gemini/settings.json` context |
| Cursor / Codex / other | Reference `AGENTS.md` and `skills/auto/SKILL.md` |
| Any harness | `bash scripts/skill-dispatch.sh auto` returns the protocol body |
