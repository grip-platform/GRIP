---
description: "Load grip skill grip-anywhere: Point GRIP at any GitHub repository \u2014 analyse repo health, surface P0/P1 issues, generate an RSI improvement plan WITHOUT touching upstream. Stealth-by-default."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/grip-anywhere/SKILL.md"
---

# /grip-anywhere

Load `grip-anywhere` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/grip-anywhere/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
