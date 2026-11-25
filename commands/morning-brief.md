---
description: "Load grip skill morning-brief: Closed-loop morning brief \u2014 GRIP orchestrates HAL orchestrates Google Calendar. Produces a structured start-of-day summary covering today's events, remaining sprint work, and one recommended first action."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/morning-brief/SKILL.md"
---

# /morning-brief

Load `morning-brief` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/morning-brief/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
