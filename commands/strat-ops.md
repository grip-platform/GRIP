---
description: "Load grip skill strat-ops: Strategy operations mode - detects strategic context and routes to appropriate strategy skill/mode. Covers gap analysis, red-teaming, pre-mortems, and decision locking."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/strat-ops/SKILL.md"
---

# /strat-ops

Load `strat-ops` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/strat-ops/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
