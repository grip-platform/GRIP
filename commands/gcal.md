---
description: "Load grip skill gcal: Fetch Google Calendar events for any CodeTonight workspace user via Domain-Wide Delegation, with GRIP-First memory enrichment and plain-language action recommendations."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/gcal/SKILL.md"
---

# /gcal

Load `gcal` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/gcal/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
