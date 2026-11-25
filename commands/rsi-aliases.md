---
description: "Load grip skill rsi-aliases: Alias commands for RSI session launch and autonomous convergence."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/rsi-aliases/SKILL.md"
---

# /rsi-aliases

Load `rsi-aliases` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/rsi-aliases/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
