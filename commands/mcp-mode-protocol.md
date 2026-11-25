---
description: "Load grip skill mcp-mode-protocol: Mode management protocol for Web/Mobile surfaces via MCP tools"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/mcp-mode-protocol/SKILL.md"
---

# /mcp-mode-protocol

Load `mcp-mode-protocol` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/mcp-mode-protocol/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
