---
description: "Load grip skill context-extension: Adaptive Context Extension (ACE) - legacy model switching (current Opus defaults to 1M)"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/context-extension/SKILL.md"
---

# /context-extension

Load `context-extension` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/context-extension/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
