---
description: "Load grip skill test-driven-agents: Pattern documentation for parallel TDD agents. Aspirational reference for future implementation."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/test-driven-agents/SKILL.md"
---

# /test-driven-agents

Load `test-driven-agents` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/test-driven-agents/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
