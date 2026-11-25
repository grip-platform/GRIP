---
description: "Load grip skill meta-improvement-switch: Protocol for pausing project work to enhance ~/.claude infrastructure when generalizable patterns are detected. Use when pattern should become permanent, user says make this permanent, or self-discovered optimisation."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/meta-improvement-switch/SKILL.md"
---

# /meta-improvement-switch

Load `meta-improvement-switch` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/meta-improvement-switch/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
