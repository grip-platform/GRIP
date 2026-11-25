---
description: "Load agent-skill skill source-command-configuring-github-actions: Load grip skill configuring-github-actions: Automatically configure GitHub Actions CI/CD workflows for any repository using global templates. Use when setting up a new repository or user invokes /setup-ci."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-configuring-github-actions/SKILL.md"
---

# /configuring-github-actions

Load `source-command-configuring-github-actions` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-configuring-github-actions/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
