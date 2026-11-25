---
description: "Load grip skill path-portability: Prevent hardcoded absolute paths in code for cross-environment compatibility"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/path-portability/SKILL.md"
---

# /path-portability

Load `path-portability` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/path-portability/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
