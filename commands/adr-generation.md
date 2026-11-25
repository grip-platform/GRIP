---
description: "Load grip skill adr-generation: Create and manage Architecture Decision Records (ADRs). Use when recording a significant architectural decision, documenting a technology choice with its rationale, or asked for an ADR or decision record."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/adr-generation/SKILL.md"
---

# /adr-generation

Load `adr-generation` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/adr-generation/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
