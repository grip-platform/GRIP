---
description: "Load grip skill evolutionary-refactor: Bio-mimicry evolutionary system for GRIP components. Scans skills, agents, modes, and MCP tools for fitness, detects duplicates, validates naming, and proposes prune/rename/enhance/add actions. Archive, never delete."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/evolutionary-refactor/SKILL.md"
---

# /evolutionary-refactor

Load `evolutionary-refactor` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/evolutionary-refactor/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
