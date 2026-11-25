---
description: "Load grip skill legal-provenance: Deterministic citation provenance for legal work \u2014 prove that every quote in a memo, opinion, or contract analysis actually exists, verbatim, in its cited statute or judgment."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/legal-provenance/SKILL.md"
---

# /legal-provenance

Load `legal-provenance` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/legal-provenance/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
