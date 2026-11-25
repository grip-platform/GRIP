---
description: "Load agent-skill skill guild-knowledge-scan: Unified Guild knowledge pipeline \u2014 crawl Discord channels, extract insights from bot interactions, enrich conversation graph with semantic embeddings, report status. Run on-demand or via /loop 4h for continuous learning."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/guild-knowledge-scan/SKILL.md"
---

# /guild-knowledge-scan

Load `guild-knowledge-scan` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/guild-knowledge-scan/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
