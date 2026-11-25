---
description: One-shot finish-a-feature lifecycle orchestrator. Takes an in-flight feature/PR/branch and drives it to DONE — verify-canonical, finish/upgrade, optional delight, seal, PR, AGORA-review, AGORA-green merge (bootstrap-class human-merged), deploy, and anti-drift close.
disable-model-invocation: false
harness: universal
---

# /finish

Load and execute the finish skill:

```
Skill(skill="finish")
```

`/finish` takes one in-flight piece of work and drives it all the way to
**DONE** — finished, sealed, reviewed, merged, deployed, verified — at the
lowest token cost while making maximum use of GRIP's convergence kernel.

See `skills/finish/SKILL.md` for the full lifecycle, safety envelope,
anti-patterns, and falsification clause.

## Cross-harness invocation

| Harness | Invocation |
|---------|------------|
| Claude Code | `/finish` |
| Aider | `run finish`, `execute finish skill`, or `Skill(skill="finish")` |
| OpenCode | Auto-loaded from `~/.claude/skills/`; use `/skill finish` |
| Gemini CLI | `/finish` if configured in `.gemini/settings.json` context |
| Cursor / Codex / other | Reference `AGENTS.md` and `skills/finish/SKILL.md` |
| Any harness | `bash scripts/skill-dispatch.sh finish` returns the protocol body |
