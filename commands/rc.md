---
description: "Load grip skill rc: Remote-control THIS Claude Code session from your phone \u2014 continue your CURRENT conversation (with its context) over WhatsApp/Slack/Discord, not a blank remote sub-session."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/rc/SKILL.md"
---

# /rc

Load `rc` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/rc/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
