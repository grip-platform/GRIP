---
description: Audits whether a codebase is actually ready to be released, by reading it from a stranger's position rather than an author's. Separates the four rungs a release audit normally collapses - built, bound, proven, and verifiable-by-a-stranger - then hands every finding to an independent agent whose default verdict is REFUTED. Read-only and zero-footprint by construction.
disable-model-invocation: false
harness: universal
---

# /infiltrate

Load and execute the infiltrate skill:

```
Skill(skill="infiltrate")
```

`/infiltrate` answers one question - can this ship - and answers it from outside. An
audit assembled by the people who built the thing is reliably wrong in one direction,
because the author supplies from memory what the artefact does not supply from itself.
This one enters read-only, takes nothing, and reports only what the shipping surface
alone establishes.

It separates four rungs that almost every release audit collapses into one. **Built** is
that the code exists and does the thing, and it is the rung that matters least.
**Bound** is that something in the shipping configuration actually invokes it, because a
gate present in a hooks directory but absent from the settings that load hooks is
decoration. **Proven** is that a test goes red when the thing breaks, which green does
not establish. **Verifiable by a stranger** is that a third party who does not trust you
can check it using only what ships - the rung with commercial weight, and the one a
signature made with a shared secret fails while passing all three below it. Sitting on
rung two while the README claims rung four is not a code defect; it is a false statement,
and it is the finding worth the whole run.

Every finding is then handed to a different agent that starts from REFUTED and must be
convinced. An auditor rewarded for findings inflates them, and that is an incentive
rather than a character flaw, so the defence is a hostile second reader and a recorded
overreach rate rather than an instruction to be careful.

See `skills/infiltrate/SKILL.md` for the eleven-step workflow, the generic lane set, the
four named traps, the worked example and the falsification clause.
`skills/infiltrate/workflow-template.js` is the copy-adaptable fan-out: edit the target
block and the lane list, then pass it to the Workflow tool.

## Cross-harness invocation

| Harness | Invocation |
|---------|------------|
| Claude Code | `/infiltrate` or `Skill(skill="infiltrate")` |
| Aider | `run infiltrate` (Aider has no custom slash commands; use `run`) |
| OpenCode | Auto-loaded from `~/.claude/skills/`; use `/skill infiltrate` |
| Gemini CLI | `/infiltrate` if configured in `.gemini/settings.json` context |
| Cursor / Codex / other | Reference `AGENTS.md` and `skills/infiltrate/SKILL.md` |
| Any harness | `bash scripts/skill-dispatch.sh infiltrate` returns the protocol body |
