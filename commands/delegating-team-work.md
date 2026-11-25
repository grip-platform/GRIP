---
description: "Load grip skill delegating-team-work: Structured team delegation with capability matching, GitHub assignment, and Slack notification. Use when distributing backlog items to team members."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/delegating-team-work/SKILL.md"
---

# /delegating-team-work

Load `delegating-team-work` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/delegating-team-work/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
