---
description: "Load grip skill authentic-writing: Prevents AI-generated fluff in external content. Opt-in via onboarding. Use when writing LinkedIn posts, Medium articles, public documentation."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/authentic-writing/SKILL.md"
---

# /authentic-writing

Load `authentic-writing` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/authentic-writing/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
