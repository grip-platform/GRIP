---
description: "Load grip skill launchd-automation: Create self-reloading macOS launchd agents with automatic plist validation. Use when user needs scheduled tasks, cron jobs, or nightly automation on macOS."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/launchd-automation/SKILL.md"
---

# /launchd-automation

Load `launchd-automation` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/launchd-automation/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
