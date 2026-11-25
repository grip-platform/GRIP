---
description: "Load grip skill batch-edit-enforcer: Enforces batch MultiEdit operations instead of multiple individual Edit calls"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/batch-edit-enforcer/SKILL.md"
---

# /batch-edit-enforcer

Load `batch-edit-enforcer` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/batch-edit-enforcer/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
