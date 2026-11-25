---
description: "Load agent-skill skill source-command-planning-mode-orchestration: Load agent-skill skill planning-mode-orchestration: Routes daily planning through prioritisation hierarchy and team assignment. Use when /mode planning is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-planning-mode-orchestration/SKILL.md"
---

# /planning-mode-orchestration

Load `source-command-planning-mode-orchestration` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-planning-mode-orchestration/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
