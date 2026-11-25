---
description: "Load agent-skill skill source-command-cleaning-node-processes: Load grip skill cleaning-node-processes: Safely identify and terminate lingering Node.js processes consuming excessive memory. Use when system is slow, ports are blocked, or user invokes /node-clean."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-cleaning-node-processes/SKILL.md"
---

# /cleaning-node-processes

Load `source-command-cleaning-node-processes` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-cleaning-node-processes/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
