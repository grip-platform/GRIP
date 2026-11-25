---
description: "Load grip skill decision-mode: Routes structured decisions through bias-aware weighted analysis with cognitive checks, the operator locking criteria and close calls through the /hitl family (/asking-users, /creating-wizards, /hitl-decision-matrix), and a council gate that decides mechanically first whether a /broly council should sit. Use when /mode decision is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/decision-mode/SKILL.md"
---

# /decision-mode-orchestration

Load `decision-mode` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/decision-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
