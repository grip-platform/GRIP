---
description: "Load grip skill hypothesis-validation: Systematically test assumptions by stating them as falsifiable hypotheses with a validation plan. Use when an assumption is driving a decision and needs testing before it is trusted."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/hypothesis-validation/SKILL.md"
---

# /hypothesis-validation

Load `hypothesis-validation` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/hypothesis-validation/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
