---
description: "Load agent-skill skill source-command-enforcing-agentic-protocols: Load grip skill enforcing-agentic-protocols: Agentic execution protocols for Codex with verification gates, rollback mechanisms, and confidence thresholds. Triggers on any destructive or high-risk operations."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-enforcing-agentic-protocols/SKILL.md"
---

# /enforcing-agentic-protocols

Load `source-command-enforcing-agentic-protocols` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-enforcing-agentic-protocols/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
