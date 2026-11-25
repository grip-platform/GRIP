---
description: "Load grip skill flow-mode: Trust-based bypass permissions earned through onboarding journey. Use when user asks about flow mode, bypass permissions, or wants to skip prompts."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/flow-mode/SKILL.md"
---

# /flow-mode

Load `flow-mode` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/flow-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
