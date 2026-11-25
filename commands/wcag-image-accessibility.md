---
description: "Load grip skill wcag-image-accessibility: Audit and fix WCAG image accessibility compliance (alt text, contrast). Use when user mentions WCAG, image accessibility, or invokes /audit-image-accessibility."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/wcag-image-accessibility/SKILL.md"
---

# /wcag-image-accessibility

Load `wcag-image-accessibility` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/wcag-image-accessibility/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
