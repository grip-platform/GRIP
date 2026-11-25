---
description: "Load grip skill kono-memory: Show KONO memory substrate statistics and session activity. Use when invoking /kono or asking about KONO memory status, size, or health."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/kono-memory/SKILL.md"
---

# /kono-memory

Load `kono-memory` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/kono-memory/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
