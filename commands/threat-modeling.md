---
description: "Load grip skill threat-modeling: STRIDE-based threat analysis for security assessment. Use when reviewing a new system or feature for security risk, or running an architecture security assessment."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/threat-modeling/SKILL.md"
---

# /threat-modeling

Load `threat-modeling` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/threat-modeling/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
