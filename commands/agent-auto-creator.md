---
description: "Load grip skill agent-auto-creator: Recursive meta-skill that detects workflow patterns and automatically generates Claude Code agent definitions. Use when user invokes /create-agent or patterns warrant new agent."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/agent-auto-creator/SKILL.md"
---

# /agent-auto-creator

Load `agent-auto-creator` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/agent-auto-creator/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
