---
description: "Load grip skill api-status: Show auth health, rate-limit state, and per-provider availability from local files only (no browser, no OAuth). Use when something feels slow, a loop is stalling, or an error needs diagnosing before blaming the network."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/api-status/SKILL.md"
---

# /api-status

Load `api-status` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/api-status/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
