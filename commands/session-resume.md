---
description: "Load grip skill session-resume: GRIP bridge for session resumption. Enables grip resume latest, grip resume gen:N, and --fresh flags. Maintains continuity across sessions."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/session-resume/SKILL.md"
---

# /session-resume

Load `session-resume` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/session-resume/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
