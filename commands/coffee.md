---
description: "Load grip skill coffee: Easter egg session diagnostic disguised as a coffee order. Reads DELIGHT v2 telemetry and git state to brew a personalised coffee. The coffee IS the session."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/coffee/SKILL.md"
---

# /coffee

Load `coffee` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/coffee/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
