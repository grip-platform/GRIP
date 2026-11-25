---
description: "Load grip skill pre-flight-check: Pre-flight environment and constraint check before implementation. Reduces wrong-approach friction by validating credentials, git state, and project constraints before writing code."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/pre-flight-check/SKILL.md"
---

# /pre-flight-check

Load `pre-flight-check` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/pre-flight-check/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
