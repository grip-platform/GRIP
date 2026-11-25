---
description: "Load codex skill skill-installer: Install Codex skills into $CODEX_HOME/skills from a curated list or a GitHub repo path. Use when a user asks to list installable skills, install a curated skill, or install a skill from another repo (including private\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "codex"
source-path: "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-installer/SKILL.md"
---

# /skill-installer

Load `skill-installer` from `codex`.

```bash
sed -n '1,220p' ${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-installer/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
