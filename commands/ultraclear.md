---
description: "Load grip skill ultraclear: Zero-loss alternative to /compact for a full interactive context window. Instead of paying to re-read the whole window and ending up with a lossy summary, /ultraclear partitions the conversation into HOT (keep verbatim)\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/ultraclear/SKILL.md"
---

# /ultraclear

Load `ultraclear` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/ultraclear/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
