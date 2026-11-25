---
description: "Load agent-skill skill source-command-incognito-mode-orchestration: Load agent-skill skill incognito-mode-orchestration: Routes private conversations with no persistence, no external calls, and minimal logging. Use when /mode incognito is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-incognito-mode-orchestration/SKILL.md"
---

# /incognito-mode-orchestration

Load `source-command-incognito-mode-orchestration` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-incognito-mode-orchestration/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
