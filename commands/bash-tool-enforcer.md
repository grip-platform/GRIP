---
description: "Load grip skill bash-tool-enforcer: Enforces use of modern CLI tools (rg over grep, fd over find) for performance and consistency"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/bash-tool-enforcer/SKILL.md"
---

# /bash-tool-enforcer

Load `bash-tool-enforcer` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/bash-tool-enforcer/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
