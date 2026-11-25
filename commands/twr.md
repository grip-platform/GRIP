---
description: "Load grip skill twr: Pull a TWR \"Copy as context\" tower from the macOS clipboard into the GRIP session. Parses TWR's markdown block (a \"# Context:\" header, \"---\"-separated clips, code fences) into structured clips GRIP uses as working\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/twr/SKILL.md"
---

# /twr

Load `twr` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/twr/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
