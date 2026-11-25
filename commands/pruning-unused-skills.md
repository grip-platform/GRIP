---
description: "Load grip skill pruning-unused-skills: Detects and prunes auto-generated skills with zero usage, preventing skill bloat from unchecked RSI generation. Use when running RSI cycles or when skill count exceeds useful threshold."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/pruning-unused-skills/SKILL.md"
---

# /pruning-unused-skills

Load `pruning-unused-skills` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/pruning-unused-skills/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
