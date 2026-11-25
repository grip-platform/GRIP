---
description: "Load grip skill preparing-meetings: Rapid-turnaround meeting preparation with parallel research agents, contact dossiers, and structured talking points. Use when preparing for any meeting, call, or event."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/preparing-meetings/SKILL.md"
---

# /preparing-meetings

Load `preparing-meetings` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/preparing-meetings/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
