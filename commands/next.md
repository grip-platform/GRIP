---
description: "Load grip skill next: The canonical \"go to next session\" handoff \u2014 ONE command for the end-of-session ritual when auto-compact is OFF. Settles current work, then runs the existing handoff trio (/ultraprompt \u2192 /save \u2192 /ultraclear) in order\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/next/SKILL.md"
---

# /next

Load `next` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/next/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
