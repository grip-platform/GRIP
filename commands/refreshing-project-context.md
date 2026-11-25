---
description: "Load agent-skill skill source-command-refreshing-project-context: Load grip skill refreshing-project-context: Systematically rebuild mental model of any repository when starting fresh sessions. Use when session starts, user says refresh context, or invokes /refresh-context."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-refreshing-project-context/SKILL.md"
---

# /refreshing-project-context

Load `source-command-refreshing-project-context` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-refreshing-project-context/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
