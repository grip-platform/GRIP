---
description: "Load grip skill rsi-optimal: Deprecated alias \u2014 superseded by /auto and /rsi. Use those instead; this entry only redirects."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/rsi-optimal/SKILL.md"
---

# /rsi-optimal

Load `rsi-optimal` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/rsi-optimal/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
