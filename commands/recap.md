---
description: "Load grip skill recap: Plain-language summary of what shipped in a sprint or time window \u2014 from git history, formatted for client updates, sprint reviews, or weekly reports."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/recap/SKILL.md"
---

# /recap

Load `recap` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/recap/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
