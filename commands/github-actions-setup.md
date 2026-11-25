---
description: "Load grip skill github-actions-setup: Automatically configure GitHub Actions CI/CD workflows for any repository using global templates. Use when setting up a new repository or user invokes /setup-ci."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/github-actions-setup/SKILL.md"
---

# /github-actions-setup

Load `github-actions-setup` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/github-actions-setup/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
