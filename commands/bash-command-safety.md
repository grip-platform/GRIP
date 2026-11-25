---
description: "Load grip skill bash-command-safety: Prevents bash syntax errors from semicolons after command substitution in eval contexts"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/bash-command-safety/SKILL.md"
---

# /bash-command-safety

Load `bash-command-safety` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/bash-command-safety/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
