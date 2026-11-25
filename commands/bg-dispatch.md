---
description: "Load grip skill bg-dispatch: Launches background work and background experts while the main session stays free. Each launch makes two choices -- what the expert knows (a fresh brief, or the inherited conversation, plus optional notes the operator\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/bg-dispatch/SKILL.md"
---

# /bg-dispatch

Load `bg-dispatch` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/bg-dispatch/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
