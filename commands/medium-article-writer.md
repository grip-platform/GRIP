---
description: "Load grip skill medium-article-writer: Automatically generate Medium-style technical articles from projects, achievements, or workflows with consistent authentic voice and structure. Use when user invokes /write-medium-article."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/medium-article-writer/SKILL.md"
---

# /medium-article-writer

Load `medium-article-writer` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/medium-article-writer/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
