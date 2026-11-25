---
description: "Load grip skill daily-planning: Structured workflow for daily task prioritisation and team assignment, ordering work by urgency and impact. Use for a daily planning session or when assigning a day's tickets across a team."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/daily-planning/SKILL.md"
---

# /daily-planning

Load `daily-planning` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/daily-planning/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
