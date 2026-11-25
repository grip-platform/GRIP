---
description: "Load grip skill sprint-status: Live sprint dashboard using Dynamic Context Injection. Shows git state, open PRs, issues, RSI health, and task progress in real time. Use during RSI sprints or when checking session progress."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/sprint-status/SKILL.md"
---

# /sprint-status

Load `sprint-status` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/sprint-status/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
