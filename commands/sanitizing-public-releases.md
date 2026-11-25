---
description: "Load grip skill sanitizing-public-releases: >"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/sanitizing-public-releases/SKILL.md"
---

# /sanitizing-public-releases

Load `sanitizing-public-releases` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/sanitizing-public-releases/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
