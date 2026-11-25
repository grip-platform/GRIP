---
description: "Load agent-skill skill source-command-automation-mode-orchestration: Load agent-skill skill automation-mode-orchestration: Routes workflow automation through safety guardrails and recursive learning. Use when /mode automation is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-automation-mode-orchestration/SKILL.md"
---

# /automation-mode-orchestration

Load `source-command-automation-mode-orchestration` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-automation-mode-orchestration/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
