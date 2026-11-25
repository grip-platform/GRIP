---
description: "Load grip skill legal-ops: GRASP-native legal operations catalogue \u2014 playbook-aware contract review, privacy triage, claims substantiation, diligence, IP first-pass, litigation intake, regulatory watch, and AI-governance triage, each closed by a\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/legal-ops/SKILL.md"
---

# /legal-ops

Load `legal-ops` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/legal-ops/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
