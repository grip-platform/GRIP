---
description: "Load grip skill code-agentic: Agentic execution protocols for Claude Code with verification gates, rollback mechanisms, and confidence thresholds. Triggers on any destructive or high-risk operations."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/code-agentic/SKILL.md"
---

# /code-agentic

Load `code-agentic` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/code-agentic/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
