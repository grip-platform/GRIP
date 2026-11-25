---
description: "Load agent-skill skill guild-communication: Formats GRIP findings and content for AI Craftspeople Guild Discord in V>>'s authentic voice. Enforces Guild evidence principles, falsifiability, tool transparency."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/guild-communication/SKILL.md"
---

# /guild-communication

Load `guild-communication` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/guild-communication/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
