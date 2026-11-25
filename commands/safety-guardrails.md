---
description: "Load grip skill safety-guardrails: Safety protocols for autonomous and automated operations. Use when configuring an autonomous workflow, setting up CI/CD safety gates, or deciding what an unattended loop may not do."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/safety-guardrails/SKILL.md"
---

# /safety-guardrails

Load `safety-guardrails` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/safety-guardrails/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
