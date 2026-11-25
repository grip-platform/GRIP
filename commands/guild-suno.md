---
description: "Load agent-skill skill guild-suno: Auto-generate Suno music tracks at conversation checkpoints using Guild context, sentiment analysis, and the GRIP Sonic Genome (GSG) algorithm. Integrates with the Guild knowledge pipeline."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/guild-suno/SKILL.md"
---

# /guild-suno

Load `guild-suno` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/guild-suno/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
