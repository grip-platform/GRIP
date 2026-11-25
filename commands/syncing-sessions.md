---
description: "Load grip skill syncing-sessions: File-based cross-session context sharing. Concurrent Claude Code sessions write and read transfer payloads via ~/.claude/sync/ directory."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/syncing-sessions/SKILL.md"
---

# /syncing-sessions

Load `syncing-sessions` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/syncing-sessions/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
