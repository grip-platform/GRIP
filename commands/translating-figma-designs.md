---
description: "Load agent-skill skill source-command-translating-figma-designs: Load grip skill translating-figma-designs: Translate Figma designs into production code with 1:1 visual parity. Use when user mentions Figma files, design handoff, or UI implementation from designs."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-translating-figma-designs/SKILL.md"
---

# /translating-figma-designs

Load `source-command-translating-figma-designs` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-translating-figma-designs/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
