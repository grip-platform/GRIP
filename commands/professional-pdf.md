---
description: "Load grip skill professional-pdf: Generate professional PDF documents with per-organisation branding variants. Default = neutral GRIP canonical (Swiss minimalism, navy accent); CodeTonight operators auto-route to the `codetonight` variant via\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/professional-pdf/SKILL.md"
---

# /professional-pdf

Load `professional-pdf` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/professional-pdf/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
