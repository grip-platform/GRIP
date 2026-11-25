---
description: "Load grip skill dev-performance-report: Generate sprint-over-sprint developer performance reports from Jira MCP data. Compares against baseline metrics. Use for monthly or per-sprint reporting cycles."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/dev-performance-report/SKILL.md"
---

# /dev-performance-report

Load `dev-performance-report` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/dev-performance-report/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
