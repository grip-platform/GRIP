---
description: "Load grip skill reasoning-with-ai: PARAMOUNT skill for AI-augmented knowledge work \u2014 keeps human intuition central, treats AI as a thought partner rather than a decision maker. Covers the articulate-first pattern, confidence gates, and trust calibration."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/reasoning-with-ai/SKILL.md"
---

# /reasoning-with-ai

Load `reasoning-with-ai` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/reasoning-with-ai/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
