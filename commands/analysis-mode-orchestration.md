---
description: "Load agent-skill skill source-command-analysis-mode-orchestration: Load agent-skill skill analysis-mode-orchestration: Routes structured analysis through RIPER phases with systematic investigation. Use when /mode analysis is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-analysis-mode-orchestration/SKILL.md"
---

# /analysis-mode-orchestration

Load `source-command-analysis-mode-orchestration` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-analysis-mode-orchestration/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
