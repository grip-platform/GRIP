---
description: "Load grip skill github-secrets-setup: Securely upload GitHub Actions secrets via gh CLI. Use when GitHub Actions workflow requires secrets or user invokes /setup-github-secrets. NEVER commits secrets."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/github-secrets-setup/SKILL.md"
---

# /github-secrets-setup

Load `github-secrets-setup` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/github-secrets-setup/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
