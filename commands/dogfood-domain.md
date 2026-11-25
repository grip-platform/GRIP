---
description: Adds a newly announced vendor model to the HAL and GRIP catalogues and wires its harness-in-a-harness capability without letting it become a default. Covers proving the model exists from vendor primary sources, separating documented from served from working, classifying the billing path, placing the catalogue and cost rows, and the family-prefix and first-match traps.
disable-model-invocation: false
harness: universal
---

# /dogfood-domain

Load and execute the dogfood-domain skill:

```
Skill(skill="dogfood-domain")
```

`/dogfood-domain` lands a single newly announced model. It separates three claims that
are usually collapsed into one - that the vendor documents the model, that this account
can call it, and that a call with default parameters actually works - and answers them in
an order where a wrong answer is cheap. It then decides reachability deliberately: a named
tier, the family flagship, a routing chain and a council seat are four separate decisions,
and a model priced above the incumbent earns the first and none of the others until
someone measures that it is worth the difference.

Two traps get named explicitly because they produce no error when missed. Guards written
as prefix matches against the previous generation match nothing in a new one, so the
request goes out shaped for a model that is not being called. And where a resolver answers
a qualifier with the first matching entry, list position is semantics: a new id can
silently capture a qualifier that used to answer with a cheaper sibling.

See `skills/dogfood-domain/SKILL.md` for the ten-step workflow, the worked example, the
anti-patterns and the falsification clause.

## Cross-harness invocation

| Harness | Invocation |
|---------|------------|
| Claude Code | `/dogfood-domain` or `Skill(skill="dogfood-domain")` |
| Aider | `run dogfood-domain` (Aider has no custom slash commands; use `run`) |
| OpenCode | Auto-loaded from `~/.claude/skills/`; use `/skill dogfood-domain` |
| Gemini CLI | `/dogfood-domain` if configured in `.gemini/settings.json` context |
| Cursor / Codex / other | Reference `AGENTS.md` and `skills/dogfood-domain/SKILL.md` |
| Any harness | `bash scripts/skill-dispatch.sh dogfood-domain` returns the protocol body |
