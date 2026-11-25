---
description: "Load agent-skill skill source-command-optimizing-images: Load grip skill optimizing-images: Lossless image optimization using ImageOptim on macOS. Use when user mentions ImageOptim, asks to optimize images, or invokes /image-optim."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-optimizing-images/SKILL.md"
---

# /optimizing-images

Load `source-command-optimizing-images` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-optimizing-images/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
