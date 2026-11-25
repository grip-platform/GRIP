---
description: "Load agent-skill skill source-command-auditing-image-accessibility: Load grip skill auditing-image-accessibility: Audit and fix WCAG image accessibility compliance (alt text, contrast). Use when user mentions WCAG, image accessibility, or invokes /audit-image-accessibility."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-auditing-image-accessibility/SKILL.md"
---

# /auditing-image-accessibility

Load `source-command-auditing-image-accessibility` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-auditing-image-accessibility/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
