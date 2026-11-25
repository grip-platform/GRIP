---
description: "Load grip skill creating-skills: Guided skill creation with PARAMOUNT quality gates. Enforces skill-creation-best-practices automatically. Use when creating new skills or invoking /create-skill."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/creating-skills/SKILL.md"
---

# /creating-skills

Load `creating-skills` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/creating-skills/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
