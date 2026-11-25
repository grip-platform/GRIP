---
description: "Load grip skill reasoning-audit: Reason about reasoning \u2014 catalogue achievements, falsify each claim, explain in plain language. Periodic or on-demand session retrospective using coordinator-convergence duality."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/reasoning-audit/SKILL.md"
---

# /reasoning-audit

Load `reasoning-audit` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/reasoning-audit/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
