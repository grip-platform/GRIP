---
description: "Load grip skill evolve: Unified evolution dispatcher that routes EvolutionState between GRIP's learning engines. Closes 5 open feedback loops. Thin router, not monolith."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/evolve/SKILL.md"
---

# /evolve

Load `evolve` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/evolve/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
