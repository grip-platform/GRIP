---
description: "Load agent-skill skill nugrip: Safe one-command GRIP upstream upgrade. Handles git-crypt, dirty trees, stash conflicts, and post-setup automatically."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/nugrip/SKILL.md"
---

# /nugrip

Load `nugrip` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/nugrip/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
