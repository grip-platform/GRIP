---
description: "Load agent-skill skill source-command-configuring-github-secrets: Load grip skill configuring-github-secrets: Securely upload GitHub Actions secrets via gh CLI. Use when GitHub Actions workflow requires secrets or user invokes /setup-github-secrets. NEVER commits secrets."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-configuring-github-secrets/SKILL.md"
---

# /configuring-github-secrets

Load `source-command-configuring-github-secrets` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-configuring-github-secrets/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
