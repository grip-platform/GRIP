---
description: "Load grip skill generating-changelogs: Generate a changelog from git history. Use when invoking /changelog, preparing release notes, or running the scheduled daily changelog job."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/generating-changelogs/SKILL.md"
---

# /generating-changelogs

Load `generating-changelogs` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/generating-changelogs/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
