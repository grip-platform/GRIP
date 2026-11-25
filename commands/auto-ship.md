---
description: "Load grip skill auto-ship: Deprecated alias \u2014 superseded by /auto. Use /auto instead; this entry only redirects."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/auto-ship/SKILL.md"
---

# /auto-ship

Load `auto-ship` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/auto-ship/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
