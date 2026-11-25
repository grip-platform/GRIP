---
description: "Load agent-skill skill source-command-automating-self-improvement: Load grip skill automating-self-improvement: Meta-skill that automates the 10-step improvement cycle - detects patterns, generates skills, creates commands, and triggers documentation."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-automating-self-improvement/SKILL.md"
---

# /automating-self-improvement

Load `source-command-automating-self-improvement` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-automating-self-improvement/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
