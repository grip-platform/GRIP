---
description: "Load grip skill mem: OS memory projector \u2014 inspect, analyse, and converge system memory toward a stable fixed point. Integrates with HAL startup and HAL Council as always-on preflight."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/mem/SKILL.md"
---

# /mem

Load `mem` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/mem/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
