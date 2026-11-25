---
description: "Load grip skill cube-config: Configure CUBE dimensions, operational metrics, promotion thresholds, proof report schedule, and external scoring mode via multi-step wizard"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/cube-config/SKILL.md"
---

# /cube-config

Load `cube-config` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/cube-config/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
