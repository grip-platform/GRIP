---
description: "Load agent-skill skill source-command-updating-documentation-automatically: Load grip skill updating-documentation-automatically: Automatically update project documentation by synthesising session history, git commits, and current state."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-updating-documentation-automatically/SKILL.md"
---

# /updating-documentation-automatically

Load `source-command-updating-documentation-automatically` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-updating-documentation-automatically/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
