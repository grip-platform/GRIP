---
description: "Load agent-skill skill teaching-mode-orchestration: Routes educational content through pedagogical frameworks with teach-back verification. Use when /mode teaching is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/.archive/teaching-mode/SKILL.md"
---

# /teaching-mode-orchestration

Load `teaching-mode-orchestration` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/.archive/teaching-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
