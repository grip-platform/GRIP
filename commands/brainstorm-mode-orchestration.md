---
description: "Load grip skill brainstorm-mode: Routes ideation through divergent thinking with suspended criticism, with the operator in the loop at every phase gate through the /hitl family (/asking-users, /creating-wizards, /hitl-decision-matrix). Use when /mode brainstorm is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/brainstorm-mode/SKILL.md"
---

# /brainstorm-mode-orchestration

Load `brainstorm-mode` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/brainstorm-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
