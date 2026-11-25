---
description: "Load agent-skill skill source-command-cleaning-git-branches: Load grip skill cleaning-git-branches: Git branch pruning and cleanup automation. Use when user asks to clean up branches, prune git, or delete old branches."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-cleaning-git-branches/SKILL.md"
---

# /cleaning-git-branches

Load `source-command-cleaning-git-branches` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-cleaning-git-branches/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
