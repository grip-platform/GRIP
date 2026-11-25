---
description: "Load grip skill composing-jira-tickets: Composes Jira tickets in org-specific standard templates with Context, Scope, Out-of-scope, References, Definition of Done, and Downstream Requirements sections."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/composing-jira-tickets/SKILL.md"
---

# /composing-jira-tickets

Load `composing-jira-tickets` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/composing-jira-tickets/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
