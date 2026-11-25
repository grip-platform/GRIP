---
description: Writes and gates a public post in GRIP's voice - plain language, no hype, every world-event claim sourced with a URL and fetch date, opinion framed as the operator's own, a provenance block, copy-paste-ready plain text, one PDF or image per post, leak sweep and pixel check. Wraps the grip-post CLI's check, ground-check and Unicode transforms. Never posts anywhere.
disable-model-invocation: false
harness: universal
---

# /grip-post

Load and execute the grip-post skill:

```
Skill(skill="grip-post")
```

`/grip-post` takes a draft for a public surface (LinkedIn first, any public update in
general) and returns a post the operator can copy and paste by hand: plain text in
GRIP's voice, every claim about the world tied to a source URL and the date it was
fetched, opinion attributed to the operator, a provenance block naming the human and
the models, one PDF or image rendered and inspected, and the whole set written to an
outbound folder and backed up. The gate is the existing grip-post CLI
(`check`, `ground-check`, the Unicode transforms), reached through
`skills/grip-post/scripts/grip-post-gate`, which resolves the checkout without a
hardcoded path and turns the CLI's verdict into an exit code.

It never posts. Nothing leaves the outbound folder except by the operator's hand.

See `skills/grip-post/SKILL.md` for the voice rules, the sourcing rule, the workflow
checklist, the output contract, a worked example and the falsification clause.

## Cross-harness invocation

| Harness | Invocation |
|---------|------------|
| Claude Code | `/grip-post` or `Skill(skill="grip-post")` |
| Aider | `run grip-post` |
| OpenCode | Auto-loaded from `~/.claude/skills/`; use `/skill grip-post` |
| Codex | `codex` also carries the upstream plugin as `/grip-post:grip-post <verb> [text]` (the CLI alone, no voice layer) |
| Any harness | `bash scripts/skill-dispatch.sh grip-post` returns the protocol body; the gate is `skills/grip-post/scripts/grip-post-gate <verb> [text]` |
