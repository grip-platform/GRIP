---
description: "Load grip skill ship-card: Render a deterministic terminal celebration at the moment work ships \u2014 a PR merge, a sprint close, a wave landing. Use when invoking /ship-card or marking a shipping moment."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/ship-card/SKILL.md"
---

# /ship-card

Load `ship-card` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/ship-card/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
