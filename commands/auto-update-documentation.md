---
description: "Load grip skill auto-update-documentation: Automatically update project documentation by synthesising session history, git commits, and current state. Use when user invokes /update-docs, after PR creation, or on milestone completion."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/auto-update-documentation/SKILL.md"
---

# /auto-update-documentation

Load `auto-update-documentation` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/auto-update-documentation/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
