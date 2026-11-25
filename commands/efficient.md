---
description: "Load grip skill efficient: Renamed to /efficiency on 2026-10-28 -- the token optimiser plus the never-stuck, no-burn fleet watch. Use /efficiency; this entry only redirects."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/efficient/SKILL.md"
---

# /efficient

Load `efficient` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/efficient/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
