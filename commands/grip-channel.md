---
description: "Load grip skill grip-channel: Bridge WhatsApp and Slack messages into Claude Code sessions via the CC Channels API. Self-messaging pattern for remote GRIP control."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/grip-channel/SKILL.md"
---

# /grip-channel

Load `grip-channel` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/grip-channel/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
