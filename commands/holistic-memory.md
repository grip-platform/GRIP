---
description: "Load grip skill holistic-memory: Synthesised, session-inherited memory kernel. Reduces ~860 fragmented memory files + the truncating MEMORY.md index into one bounded HOLISTIC.md a fresh session inherits at zero in-session token cost, with an R1\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/holistic-memory/SKILL.md"
---

# /holistic-memory

Load `holistic-memory` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/holistic-memory/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
