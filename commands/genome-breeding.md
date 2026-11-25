---
description: "Load grip skill genome-breeding: Cross-team genome crossover through portable dict-level breeding, with safety gates. Use when invoking /breed or combining genomes from different GRIP instances."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/genome-breeding/SKILL.md"
---

# /genome-breeding

Load `genome-breeding` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/genome-breeding/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
