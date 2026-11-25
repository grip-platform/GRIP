---
description: "Load grip skill cowork-bootstrap: Auto-loads GRIP context at Cowork session start. Read this skill first when ~/.claude is mounted in Cowork."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/cowork-bootstrap/SKILL.md"
---

# /cowork-bootstrap

Load `cowork-bootstrap` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/cowork-bootstrap/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
