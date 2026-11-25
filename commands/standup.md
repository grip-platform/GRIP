---
description: "Load grip skill standup: Generate a structured standup update \u2014 yesterday's commits, today's plan, and any blockers \u2014 from git history and session context."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/standup/SKILL.md"
---

# /standup

Load `standup` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/standup/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
