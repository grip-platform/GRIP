---
description: "Load agent-skill skill upskilling-mode-orchestration: Routes meta-skill creation through recursive learning with quality gates. Use when /mode upskilling is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/.archive/upskilling-mode/SKILL.md"
---

# /upskilling-mode-orchestration

Load `upskilling-mode-orchestration` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/.archive/upskilling-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
