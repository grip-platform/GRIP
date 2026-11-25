---
description: "Load grip skill node-cleanup: Safely identify and terminate lingering Node.js processes consuming excessive memory. Use when system is slow, ports are blocked, or user invokes /node-clean."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/node-cleanup/SKILL.md"
---

# /node-cleanup

Load `node-cleanup` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/node-cleanup/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
