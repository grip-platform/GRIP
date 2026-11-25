---
description: "Load grip skill grip-notify: Cross-device push notifications from GRIP to the operator's devices. Composable multi-backend facade (macOS local + iMessage self-send + Slack bot DM) with git-crypted per-operator config. Pairs with /notify."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/grip-notify/SKILL.md"
---

# /grip-notify

Load `grip-notify` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/grip-notify/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
