---
description: "Load grip skill pr-automation: Efficient PR creation with gh CLI - automated branch, commit, push, and pr-create. Use when user asks to create PR, open pull request, or invokes /create-pr."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/pr-automation/SKILL.md"
---

# /pr-automation

Load `pr-automation` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/pr-automation/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
