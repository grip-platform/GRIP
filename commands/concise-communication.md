---
description: "Load grip skill concise-communication: Eliminates unnecessary preambles and postambles that waste tokens"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/concise-communication/SKILL.md"
---

# /concise-communication

Load `concise-communication` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/concise-communication/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
