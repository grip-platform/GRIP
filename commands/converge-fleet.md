---
description: A /converge loop whose parallel expert lanes cross-learn — each lane's discoveries are pooled and handed to the next round. Ships a copy-adaptable dynamic Workflow template with stall-timeout, scoped-negative and budget discipline baked in.
disable-model-invocation: false
harness: universal
---

# /converge-fleet

Load and execute the converge-fleet skill:

```
Skill(skill="converge-fleet")
```

`/converge-fleet` answers one question over a corpus too big for a single context by
running expert lanes in parallel over disjoint slices, pooling what each lane *learned*
(not only what it found), and feeding that union into the next converge round — so a term
discovered by one lane reaches every other lane on the next pass. The loop exits on a
stated criterion, honest dryness, or a depth budget, and an adversarial pass tries to
refute the findings before they are reported, up to a cap: any it does not reach come
back marked UNCHECKED, never as verified.

The skill is a thin orchestrator: it composes the native Workflow tool (orchestration),
`/broly` (multi-model diversity) and `lib/ultracode` (idempotent replay) as code, and takes
`/converge`'s loop **semantics** — criterion, depth budget, dry-exit. The loop shape is the
one thing the workflow template restates rather than imports, because a JavaScript workflow
script cannot call the Python kernel in `lib/converge.py`. The two genuinely new pieces are
the cross-learning barrier and the scoped-negative handling.

See `skills/converge-fleet/SKILL.md` for the mechanism, the exit conditions, the
anti-patterns and the falsification clause, and
`skills/converge-fleet/templates/converge-fleet-workflow-v2.js` for the workflow
template to copy. The v1 file beside it is superseded — its verify phase fans N
Anthropic agents into a terminal `parallel()` barrier, so a single quota limit takes
the whole phase down.

## Cross-harness invocation

| Harness | Invocation |
|---------|------------|
| Claude Code | `/converge-fleet` or `Skill(skill="converge-fleet")` |
| Aider | `run converge-fleet` (Aider has no custom slash commands; use `run`) |
| OpenCode | Auto-loaded from `~/.claude/skills/`; use `/skill converge-fleet` |
| Gemini CLI | `/converge-fleet` if configured in `.gemini/settings.json` context |
| Cursor / Codex / other | Reference `AGENTS.md` and `skills/converge-fleet/SKILL.md` |
| Any harness | `bash scripts/skill-dispatch.sh converge-fleet` returns the protocol body |
