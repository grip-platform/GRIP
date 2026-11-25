---
description: "Load grip skill caveman: Compress Claude output to ~65-75% fewer tokens using caveman-style terse prose. All technical substance preserved. Code/API names never abbreviated. Use when V>> wants faster, cheaper responses."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/caveman/SKILL.md"
---

# /caveman

Load `caveman` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/caveman/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
