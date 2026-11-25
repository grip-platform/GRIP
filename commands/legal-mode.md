---
description: "Load grip skill legal-mode: Orchestrates legal-ops dual-track workflows with jurisdiction awareness, quality gates, and the GRASP provenance gate \u2014 every legal deliverable that cites authority ships with a signed, self-verifiable legal receipt\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/legal-mode/SKILL.md"
---

# /legal-mode

Load `legal-mode` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/legal-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
