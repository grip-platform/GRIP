---
description: "Load grip skill improvement-tracking: PARAMOUNT skill for recursive self-improvement tracking. Logs improvements, detects design smells via heatmap, emits [IMPROVEMENT HEATMAP] signal when components hit 3x threshold."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/improvement-tracking/SKILL.md"
---

# /improvement-tracking

Load `improvement-tracking` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/improvement-tracking/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
