---
description: "Load grip skill backlog: Personal knowledge staging with autonomous AI processing. Drop notes, URLs, ideas, documents \u2014 GRIP categorises, analyses, and proposes actions with critical reasoning gates."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/backlog/SKILL.md"
---

# /backlog

Load `backlog` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/backlog/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
