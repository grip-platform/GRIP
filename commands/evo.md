---
description: "Load grip skill evo: Fractal facet evolution engine. Re-analyses Claude Code's /insights facet files with GRIP context to calibrate friction metrics, detect false flags, and feed calibrated insights into the learning pipeline, genome, and\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/evo/SKILL.md"
---

# /evo

Load `evo` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/evo/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
