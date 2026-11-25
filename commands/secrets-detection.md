---
description: "Load grip skill secrets-detection: Detect and prevent accidental exposure of secrets in code and output"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/secrets-detection/SKILL.md"
---

# /secrets-detection

Load `secrets-detection` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/secrets-detection/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
