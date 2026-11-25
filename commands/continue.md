---
description: "Load grip skill continue: Reconstructs the goal, approval and next step of the current session from the transcript itself -- the newest real request before the \"continue\" prompt, whether a later short reply approved a specific step, and whether\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/continue/SKILL.md"
---

# /continue

Load `continue` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/continue/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
