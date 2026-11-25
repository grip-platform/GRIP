---
description: "Load codex skill openai-docs: Use for Codex models/pricing, scheduled tasks, skills, settings, setup, troubleshooting, customization, automations, and self-knowledge\u2014including 'you,' 'your,' 'this app,' or 'this coding agent' when they refer to\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "codex"
source-path: "${CODEX_HOME:-$HOME/.codex}/skills/.system/openai-docs/SKILL.md"
---

# /openai-docs

Load `openai-docs` from `codex`.

```bash
sed -n '1,220p' ${CODEX_HOME:-$HOME/.codex}/skills/.system/openai-docs/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
