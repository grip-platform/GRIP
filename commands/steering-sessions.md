---
description: "Load grip skill steering-sessions: Non-disruptive session steering dispatcher for queuing fixes, enhancements, TODOs, and investigations during active loops."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/steering-sessions/SKILL.md"
---

# /steering-sessions

Load `steering-sessions` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/steering-sessions/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
