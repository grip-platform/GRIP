---
description: "Load grip skill marketing-mode: Routes content across formats with anti-fluff enforcement and multi-format repurposing. Use when /mode marketing is active and content creation begins."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/marketing-mode/SKILL.md"
---

# /marketing-mode

Load `marketing-mode` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/marketing-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
