---
description: "Load grip skill slack-update: Rich Block Kit update posts (features + heartbeat) to GRIP Slack. Subsumed into /update-team \u2014 features \u2192 /update-team (default), heartbeat \u2192 /update-team --heartbeat."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/slack-update/SKILL.md"
---

# /slack-update

Load `slack-update` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/slack-update/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
