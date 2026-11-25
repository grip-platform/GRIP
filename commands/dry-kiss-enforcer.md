---
description: "Load grip skill dry-kiss-enforcer: Enforces DRY and KISS principles to eliminate duplication and over-engineering"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/dry-kiss-enforcer/SKILL.md"
---

# /dry-kiss-enforcer

Load `dry-kiss-enforcer` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/dry-kiss-enforcer/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
