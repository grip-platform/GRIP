---
description: "Load grip skill mcp-apps: GRIP Emergent UI components as MCP Apps for interactive conversations. Use when presenting options, confirmations, checklists, kanban boards, decision matrices, or other interactive elements within Claude."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/mcp-apps/SKILL.md"
---

# /mcp-apps

Load `mcp-apps` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/mcp-apps/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
