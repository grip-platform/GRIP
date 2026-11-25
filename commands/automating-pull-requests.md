---
description: "Load agent-skill skill source-command-automating-pull-requests: Load grip skill automating-pull-requests: Efficient PR creation with gh CLI - automated branch, commit, push, and pr-create. Use when user asks to create PR, open pull request, or invokes /create-pr."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-automating-pull-requests/SKILL.md"
---

# /automating-pull-requests

Load `source-command-automating-pull-requests` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-automating-pull-requests/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
