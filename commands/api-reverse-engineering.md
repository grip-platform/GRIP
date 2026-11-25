---
description: "Load grip skill api-reverse-engineering: Systematically reverse engineer authenticated web APIs from browser DevTools captures. Use when user provides network request details or invokes /reverse-api. Legal when using own credentials."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/api-reverse-engineering/SKILL.md"
---

# /api-reverse-engineering

Load `api-reverse-engineering` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/api-reverse-engineering/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
