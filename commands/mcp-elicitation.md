---
description: "Load grip skill mcp-elicitation: MCP servers requesting user input mid-execution via the `elicitation/create` protocol. Enables stateful multi-step wizards through grip-channel, grip-apps, grip-pair."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/mcp-elicitation/SKILL.md"
---

# /mcp-elicitation

Load `mcp-elicitation` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/mcp-elicitation/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
