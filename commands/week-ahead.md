---
description: "Load grip skill week-ahead: Show the combined team calendar for the next 5 days \u2014 who's in what meetings, free windows, and good times for a team call."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/week-ahead/SKILL.md"
---

# /week-ahead

Load `week-ahead` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/week-ahead/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
