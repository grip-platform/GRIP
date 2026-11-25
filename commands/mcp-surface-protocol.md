---
description: "Load grip skill mcp-surface-protocol: Surface-aware rendering protocol for Web/Mobile. Instructs AI to proxy GRIP MCP tool output through native Anthropic tools and artifacts when on surfaces without iframe support."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/mcp-surface-protocol/SKILL.md"
---

# /mcp-surface-protocol

Load `mcp-surface-protocol` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/mcp-surface-protocol/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
