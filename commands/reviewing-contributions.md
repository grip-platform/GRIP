---
description: "Load grip skill reviewing-contributions: Automated contribution review system that evaluates PRs against GRIP design principles. Use when PR opened, /review-pr invoked, or contribution review needed."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/reviewing-contributions/SKILL.md"
---

# /reviewing-contributions

Load `reviewing-contributions` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/reviewing-contributions/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
