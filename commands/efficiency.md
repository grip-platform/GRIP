---
description: "Load grip skill efficiency: The be-all-and-end-all token optimiser and fleet watch. Executes any task at minimum token cost without dropping facts, and keeps every fleet, loop and workflow it starts watched so nothing sits stuck or burns tokens…"
argument-hint: "Optional task context, or 'watch' for one fleet-watch tick"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/efficiency/SKILL.md"
---

# /efficiency

Load `efficiency` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/efficiency/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
