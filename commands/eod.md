---
description: "Load grip skill eod: End-of-day wrap-up \u2014 what shipped today, what carries over to tomorrow, and an optional Slack post to close the day."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/eod/SKILL.md"
---

# /eod

Load `eod` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/eod/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
