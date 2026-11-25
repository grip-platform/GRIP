---
description: "Load grip skill slack-blocks: Slack Block Kit styling patterns for beautiful, structured messages. Use for rich notifications, dashboards, status updates, and formatted reports."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/slack-blocks/SKILL.md"
---

# /slack-blocks

Load `slack-blocks` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/slack-blocks/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
