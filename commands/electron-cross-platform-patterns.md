---
description: "Load grip skill electron-cross-platform-patterns: >"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/electron-cross-platform-patterns/SKILL.md"
---

# /electron-cross-platform-patterns

Load `electron-cross-platform-patterns` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/electron-cross-platform-patterns/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
