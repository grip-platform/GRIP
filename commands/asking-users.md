---
description: "Load grip skill asking-users: PARAMOUNT skill establishing AskUserQuestion as core GRIP feature. Use proactively for confidence gates, destructive actions, multi-step tasks, and UI/UX decisions, alongside its siblings /creating-wizards and /hitl."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/asking-users/SKILL.md"
---

# /asking-users

Load `asking-users` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/asking-users/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
