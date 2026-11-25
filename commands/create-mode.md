---
description: "Load grip skill create-mode: Guides mode and skill creation with quality gates, wizard flows, and testing protocols. Use when /mode create is active and new modes or skills are being built."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/create-mode/SKILL.md"
---

# /create-mode

Load `create-mode` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/create-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
