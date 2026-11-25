---
description: "Load grip skill branch-cleanup: Git branch pruning and cleanup automation. Use when user asks to clean up branches, prune git, or delete old branches."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/branch-cleanup/SKILL.md"
---

# /branch-cleanup

Load `branch-cleanup` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/branch-cleanup/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
