---
description: "Load grip skill pedagogical-patterns: Evidence-based teaching methods built on the FASTER framework for technical education. Use when writing a tutorial, explaining a hard concept, or designing technical teaching material."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/pedagogical-patterns/SKILL.md"
---

# /pedagogical-patterns

Load `pedagogical-patterns` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/pedagogical-patterns/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
