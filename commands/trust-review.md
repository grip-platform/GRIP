---
description: "Load grip skill trust-review: Run GRIP governance audit answering 6 accountability questions with evidence from the live installation. Use when demonstrating governance to prospects, auditing system health, or preparing for security reviews."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/trust-review/SKILL.md"
---

# /trust-review

Load `trust-review` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/trust-review/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
