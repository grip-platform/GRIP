---
description: "Load codex skill plugin-creator: Create and scaffold plugin directories for Codex with a required `.codex-plugin/plugin.json`, optional plugin folders/files, valid manifest defaults, and personal-marketplace entries by default."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "codex"
source-path: "${CODEX_HOME:-$HOME/.codex}/skills/.system/plugin-creator/SKILL.md"
---

# /plugin-creator

Load `plugin-creator` from `codex`.

```bash
sed -n '1,220p' ${CODEX_HOME:-$HOME/.codex}/skills/.system/plugin-creator/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
