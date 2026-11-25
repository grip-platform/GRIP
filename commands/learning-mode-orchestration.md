---
description: "Load agent-skill skill source-command-learning-mode-orchestration: Load agent-skill skill learning-mode-orchestration: Routes personal learning pathway design through assessment and spaced repetition. Use when /mode learning is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-learning-mode-orchestration/SKILL.md"
---

# /learning-mode-orchestration

Load `source-command-learning-mode-orchestration` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-learning-mode-orchestration/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
