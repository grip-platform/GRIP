---
description: "Load grip skill check-reachable: Browse the full GRIP catalog of skills, modes and agents \u2014 every entry tagged by kind, including the ones never mentioned in CLAUDE.md or rules/*.md."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/check-reachable/SKILL.md"
---

# /check-reachable

Load `check-reachable` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/check-reachable/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
