---
description: "Load grip skill team-digest: Subsumed into /update-team --digest (canonical). Inbound per-member Slack channel-activity digest \u2014 each member gets others' messages only (self-excluded) with deep links."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/team-digest/SKILL.md"
---

# /team-digest

Load `team-digest` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/team-digest/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
