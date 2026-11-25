---
description: "Load agent-skill skill source-command-creating-agents-automatically: Load grip skill creating-agents-automatically: Recursive meta-skill that detects workflow patterns and automatically generates Codex agent definitions. Use when user invokes /create-agent or patterns warrant new agent."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-creating-agents-automatically/SKILL.md"
---

# /creating-agents-automatically

Load `source-command-creating-agents-automatically` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-creating-agents-automatically/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
