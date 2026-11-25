---
description: "Load grip skill world-builder: Create and manage generative game zones for Dorothy's Pokemon-style world. Use this skill when creating, updating, or designing game worlds via MCP tools."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/world-builder/SKILL.md"
---

# /world-builder

Load `world-builder` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/world-builder/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
