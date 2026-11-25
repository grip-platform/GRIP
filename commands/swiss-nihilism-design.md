---
description: "Load grip skill swiss-nihilism-design: >"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/swiss-nihilism-design/SKILL.md"
---

# /swiss-nihilism-design

Load `swiss-nihilism-design` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/swiss-nihilism-design/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
