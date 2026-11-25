---
description: "Load grip skill figma-to-code: Translate Figma designs into production code with 1:1 visual parity. Use when user mentions Figma files, design handoff, or UI implementation from designs."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/figma-to-code/SKILL.md"
---

# /figma-to-code

Load `figma-to-code` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/figma-to-code/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
