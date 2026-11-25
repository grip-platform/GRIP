---
description: "Load grip skill evo-tool-limitation-calibrator: Distinguishes genuine tool limitations from intentional GRIP gate blocks during /evo calibration. Provides evidence keywords that dynamically enrich the calibration pipeline."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/evo-tool-limitation-calibrator/SKILL.md"
---

# /evo-tool-limitation-calibrator

Load `evo-tool-limitation-calibrator` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/evo-tool-limitation-calibrator/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
