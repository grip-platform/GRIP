---
description: "Load grip skill save-exit: Runs unified state persistence via the saving-state skill, then prints an actionable context-aware goodbye banner instructing the operator to run /compact or /exit manually."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/save-exit/SKILL.md"
---

# /save-exit

Load `save-exit` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/save-exit/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
