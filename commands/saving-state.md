---
description: "Load grip skill saving-state: Unified one-shot save of all GRIP memory subsystems. Persists insights, genome DNA, instance, KONO, and session context in one command. Mid-session safe."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/saving-state/SKILL.md"
---

# /saving-state

Load `saving-state` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/saving-state/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
