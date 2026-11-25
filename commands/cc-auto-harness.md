---
description: "Load grip skill cc-auto-harness: Meta-RSI skill that detects new Claude Code features and converges them into GRIP capabilities. Use when invoking /cc-auto-harness, after a Claude Code upgrade, or to check which substrate features GRIP has not yet\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/cc-auto-harness/SKILL.md"
---

# /cc-auto-harness

Load `cc-auto-harness` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/cc-auto-harness/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
