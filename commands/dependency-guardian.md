---
description: "Load grip skill dependency-guardian: Prevents reading from node_modules, venv, and other dependency folders that waste 50k+ tokens"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/dependency-guardian/SKILL.md"
---

# /dependency-guardian

Load `dependency-guardian` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/dependency-guardian/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
