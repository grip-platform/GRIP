---
description: "Load agent-skill skill source-command-evolve-broadcast: Load grip skill evolve-broadcast: Broadcast /evolve results to Guild Discord and Slack with epistemic framing"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-evolve-broadcast/SKILL.md"
---

# /evolve-broadcast

Load `source-command-evolve-broadcast` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-evolve-broadcast/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
