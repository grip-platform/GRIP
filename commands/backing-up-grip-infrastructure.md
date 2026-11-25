---
description: "Load grip skill backing-up-grip-infrastructure: Creates tiered GRIP backups (quick/full/complete) and restores with conflict handling. Use when invoking /backup-grip, before upgrades, or for disaster recovery."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/backing-up-grip-infrastructure/SKILL.md"
---

# /backing-up-grip-infrastructure

Load `backing-up-grip-infrastructure` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/backing-up-grip-infrastructure/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
