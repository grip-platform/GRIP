---
description: "Load grip skill copy-cc-clean: Strips leading whitespace and Claude Code quote-block artefacts (\u258e \u258c \u258f \u2502) from every line in the system clipboard and copies the cleaned result back."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/copy-cc-clean/SKILL.md"
---

# /copy-cc-clean

Load `copy-cc-clean` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/copy-cc-clean/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
