---
description: "Load grip skill markdown-expert: Enforce markdown linting standards for all documentation to prevent common AI mistakes like missing code language tags, emphasis as headings, and missing blank lines"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/markdown-expert/SKILL.md"
---

# /markdown-expert

Load `markdown-expert` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/markdown-expert/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
