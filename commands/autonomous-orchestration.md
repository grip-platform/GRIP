---
description: "Load grip skill autonomous-orchestration: Framework for orchestrating autonomous AI workflows through DEFINE, VALIDATE, EXECUTE, MONITOR, COMPLETE, LEARN with RSI feedback loops. Use when designing or running a long autonomous task pipeline."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/autonomous-orchestration/SKILL.md"
---

# /autonomous-orchestration

Load `autonomous-orchestration` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/autonomous-orchestration/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
