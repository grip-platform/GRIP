---
description: "Load grip skill session-state-persistence: Auto-update session state in instance JSON next_up field on significant milestones. Enables seamless cross-session continuity. Triggers at 80% context gate and milestone completion."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/session-state-persistence/SKILL.md"
---

# /session-state-persistence

Load `session-state-persistence` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/session-state-persistence/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
