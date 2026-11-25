---
description: "Load grip skill remind-me: Durable operator reminders \u2014 \"remind me later/at X about Y\" arms a reminder on BOTH the always-on VPS (systemd one-shot -> Slack DM + Discord via the relay bots; fires even when the Mac is off) and the Mac's launchd\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/remind-me/SKILL.md"
---

# /remind-me

Load `remind-me` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/remind-me/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
