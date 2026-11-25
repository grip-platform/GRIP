---
description: "Load grip skill decision-locking: Finalise and document strategic decisions to prevent drift. Use after red-team exercises, gap analysis, or when team reaches consensus."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/decision-locking/SKILL.md"
---

# /decision-locking

Load `decision-locking` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/decision-locking/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
