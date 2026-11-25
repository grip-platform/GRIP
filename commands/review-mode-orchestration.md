---
description: "Load agent-skill skill source-command-review-mode-orchestration: Load agent-skill skill review-mode-orchestration: Routes unified review across code, content, documents, and designs. Use when /mode review is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-review-mode-orchestration/SKILL.md"
---

# /review-mode-orchestration

Load `source-command-review-mode-orchestration` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-review-mode-orchestration/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
