---
description: "Load grip skill execution-router: Routes tasks to optimal execution patterns based on complexity assessment (Direct/Focused/Sprint/Full Mesh)"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/execution-router/SKILL.md"
---

# /execution-router

Load `execution-router` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/execution-router/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
