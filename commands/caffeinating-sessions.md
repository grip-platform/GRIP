---
description: "Load grip skill caffeinating-sessions: Prevents macOS sleep during long RSI loops and autonomous sessions. Use when starting RSI sprints, pair-mode marathons, or when user invokes /caffeinate."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/caffeinating-sessions/SKILL.md"
---

# /caffeinating-sessions

Load `caffeinating-sessions` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/caffeinating-sessions/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
