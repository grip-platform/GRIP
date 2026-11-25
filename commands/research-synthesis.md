---
description: "Load grip skill research-synthesis: Aggregate and synthesise findings across multiple sources. Use for literature review, meta-analysis, or combining research from several places into one grounded conclusion."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/research-synthesis/SKILL.md"
---

# /research-synthesis

Load `research-synthesis` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/research-synthesis/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
