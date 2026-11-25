---
description: "Load grip skill ultrareview: Context-aware parallel code review \u2014 runs specialist review agents in parallel (security, design principles, BIG-O, test coverage) locally without requiring the web. Shows diffstat before starting."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/ultrareview/SKILL.md"
---

# /ultrareview

Load `ultrareview` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/ultrareview/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
