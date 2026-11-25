---
description: "Load grip skill self-improvement-engine: Meta-skill that automates the 10-step improvement cycle - detects patterns, generates skills, creates commands, and triggers documentation. Use when user invokes /auto-improve or says detect inefficiency."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/self-improvement-engine/SKILL.md"
---

# /self-improvement-engine

Load `self-improvement-engine` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/self-improvement-engine/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
