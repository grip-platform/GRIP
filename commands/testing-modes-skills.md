---
description: "Load grip skill testing-modes-skills: Validates mode YAML syntax, skill dependencies, and token budgets. Use when testing GRIP modes/skills or after infrastructure changes."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/testing-modes-skills/SKILL.md"
---

# /testing-modes-skills

Load `testing-modes-skills` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/testing-modes-skills/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
