---
description: "Load grip skill prompt-routing: Auto-assesses every non-exempt user prompt and recommends the optimal execution pattern (Direct/Focused/Sprint/FullMesh)."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/prompt-routing/SKILL.md"
---

# /prompt-routing

Load `prompt-routing` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/prompt-routing/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
