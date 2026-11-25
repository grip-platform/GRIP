---
description: "Load grip skill approach-verification: Pre-implementation gate that catches wrong-approach errors before code is written. Fires on multi-step tasks touching 3+ files. Use before starting a multi-file change, refactor, or migration."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/approach-verification/SKILL.md"
---

# /approach-verification

Load `approach-verification` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/approach-verification/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
