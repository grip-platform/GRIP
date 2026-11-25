---
description: "Load grip skill concurrent-session-safety: Prevent data loss and drift when several Claude Code sessions share one ~/.claude directory on different branches. Covers stash cross-contamination, HEAD churn, and worktree discipline."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/concurrent-session-safety/SKILL.md"
---

# /concurrent-session-safety

Load `concurrent-session-safety` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/concurrent-session-safety/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
