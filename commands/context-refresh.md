---
description: "Load grip skill context-refresh: Systematically rebuild mental model of any repository when starting fresh sessions. Use when session starts, user says refresh context, or invokes /refresh-context. Eliminates cold start problem in <3500 tokens."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/context-refresh/SKILL.md"
---

# /context-refresh

Load `context-refresh` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/context-refresh/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
