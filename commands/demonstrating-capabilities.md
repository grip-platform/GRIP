---
description: "Load agent-skill skill source-command-demonstrating-capabilities: Load grip skill demonstrating-capabilities: Live demonstration protocols for GRIP system showcasing. Use when presenting to prospects, teams, or recording tutorials."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-demonstrating-capabilities/SKILL.md"
---

# /demonstrating-capabilities

Load `source-command-demonstrating-capabilities` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-demonstrating-capabilities/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
