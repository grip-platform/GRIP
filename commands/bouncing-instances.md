---
description: "Load grip skill bouncing-instances: Cyclical renewal for GRIP through the Big Bounce pattern. Resets system while preserving essential infrastructure and patterns. Use when invoking /bounce or requesting fresh start."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/bouncing-instances/SKILL.md"
---

# /bouncing-instances

Load `bouncing-instances` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/bouncing-instances/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
