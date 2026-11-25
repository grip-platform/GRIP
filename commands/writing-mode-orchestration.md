---
description: "Load agent-skill skill writing-mode-orchestration: Routes long-form writing through voice-consistent editing protocols. Use when /mode writing is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/.archive/writing-mode/SKILL.md"
---

# /writing-mode-orchestration

Load `writing-mode-orchestration` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/.archive/writing-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
