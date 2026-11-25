---
description: "Load grip skill slack-feature-updates: Generate and post Block Kit feature updates to the CodeTonight Slack workspace. Auto-discovers PRs/commits/issues for a given period and formats them as rich, categorised updates."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/slack-feature-updates/SKILL.md"
---

# /slack-feature-updates

Load `slack-feature-updates` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/slack-feature-updates/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
