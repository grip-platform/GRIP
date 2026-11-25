---
description: "Load grip skill strategy-mode: Routes strategic assessment through gap analysis, red-teaming, and decision locking. Use when /mode strategy is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/strategy-mode/SKILL.md"
---

# /strategy-mode

Load `strategy-mode` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/strategy-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
