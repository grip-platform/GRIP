---
description: "Load grip skill design-smell-response: PARAMOUNT skill for responding to [IMPROVEMENT HEATMAP] signals. Defines protocol for reviewing components improved 3+ times, SRP analysis, refactor decisions, and effectiveness verification."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/design-smell-response/SKILL.md"
---

# /design-smell-response

Load `design-smell-response` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/design-smell-response/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
