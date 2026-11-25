---
description: "Load grip skill hitl-decision-matrix: Interactive human-in-the-loop decision matrix wizard. Converts N pre-computed decision defaults (with rationale + falsifier) into a batched AskUserQuestion flow with ELI5 framing, cascade detection, confirmation step,\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/hitl-decision-matrix/SKILL.md"
---

# /hitl-decision-matrix

Load `hitl-decision-matrix` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/hitl-decision-matrix/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
