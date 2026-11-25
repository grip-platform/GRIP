---
description: "Load grip skill recursive-learning: Learn from error patterns, generate skills to prevent recurrence, improve own learning algorithm - true recursive self-improvement"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/recursive-learning/SKILL.md"
---

# /recursive-learning

Load `recursive-learning` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/recursive-learning/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
