---
description: "Load agent-skill skill source-command-create-mode-orchestration: Load grip skill create-mode-orchestration: Guides mode and skill creation with quality gates, wizard flows, and testing protocols. Use when /mode create is active and new modes or skills are being built."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-create-mode-orchestration/SKILL.md"
---

# /create-mode-orchestration

Load `source-command-create-mode-orchestration` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-create-mode-orchestration/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
