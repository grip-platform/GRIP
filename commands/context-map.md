---
description: "Load grip skill context-map: Generate and maintain path-scoped context rules for feature areas. Proactive context injection so Claude understands multi-actor flows before writing code."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/context-map/SKILL.md"
---

# /context-map

Load `context-map` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/context-map/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
