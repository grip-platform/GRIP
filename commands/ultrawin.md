---
description: Capstone autonomous-delivery meta-skill — recon → parallel implement/verify/AGORA-merge → specialist fleet → auto-deploy → report. Thin orchestrator over existing kernels; risky items held for review.
disable-model-invocation: false
harness: universal
---

# /ultrawin

Load and execute the ultrawin skill:

```
Skill(skill="ultrawin")
```

`/ultrawin` takes a goal (or the open safe-auto backlog) from intent to
verified-merged wins with maximum safe parallelism and zero theatre. It composes
existing kernels (`/auto`, `/converge`, `/finish`, the Workflow fan-out pattern,
AGORA-gating, the loop-engineering converge kernel) and reimplements nothing.

See `skills/ultrawin/SKILL.md` for the full lifecycle, safety envelope,
anti-theatre gate, and falsification clause.

## Cross-harness invocation

| Harness | Invocation |
|---------|------------|
| Claude Code | `/ultrawin` or `Skill(skill="ultrawin")` |
| Aider | `run ultrawin` or `execute ultrawin skill` (Aider has no custom slash commands; use `run`) |
| OpenCode | Auto-loaded from `~/.claude/skills/`; use `/skill ultrawin` |
| Gemini CLI | `/ultrawin` if configured in `.gemini/settings.json` context |
| Cursor / Codex / other | Reference `AGENTS.md` and `skills/ultrawin/SKILL.md` |
| Any harness | `bash scripts/skill-dispatch.sh ultrawin` returns the protocol body |
